"""Naive single-pass RAG chain (spec section 3/10 — simple query path).

Flow: rewrite -> embed -> retrieve -> ground -> generate -> cite.

Citation guarantees (enforced here, not left to prompt discipline):
- The model sees numbered excerpts; every claim is expected to carry [n].
- Parsed citations are validated against the actually-retrieved chunk set:
  out-of-range or duplicate markers are dropped, and each emitted citation
  maps 1:1 to a real RetrievedChunk (source_id, title, excerpt, url).
- If retrieval returns nothing, generation is skipped entirely and the chain
  reports insufficient evidence instead of letting the model improvise.
- If the model signals INSUFFICIENT_EVIDENCE, citations are cleared and a
  plain-language refusal is returned — invented facts never get dressed up
  as sourced ones.

Output fields mirror the spec's QueryResponse contract (answer, citations,
agent_path, trace_url).
"""

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

from config.settings import Settings, get_settings
from llm_providers.base import GenerationResult, ProviderUnavailableError
from models.schemas import RetrievedChunk
from observability.langfuse_wrapper import NULL_TRACER, Tracer
from retrieval.base import VectorStore

INSUFFICIENT_MARKER = "INSUFFICIENT_EVIDENCE"
_CITATION_RE = re.compile(r"\[(\d{1,2})\]")
_EXCERPT_CHARS_IN_CITATION = 300

DEFAULT_SYSTEM_PROMPT = """You are Sentinel, a financial research assistant. You answer \
ONLY using the numbered excerpts provided in the user message.

Rules:
1. Cite supporting excerpts inline with their numbers in brackets — e.g. [1] or [2][3] — \
for every factual claim.
2. Never invent figures, dates, entities, or events that are not present in the excerpts.
3. If the excerpts do not contain enough information to answer the question, begin your \
reply with INSUFFICIENT_EVIDENCE followed by one short sentence about what is missing.
4. Quote precise figures with their stated periods rather than rounding."""


@dataclass
class RagAnswer:
    answer: str
    citations: list[dict] = field(default_factory=list)
    agent_path: list[str] = field(default_factory=list)
    trace_url: str | None = None
    retrieved_chunks: list[RetrievedChunk] = field(default_factory=list)
    rewritten_query: str | None = None
    filters_used: dict = field(default_factory=dict)
    insufficient_evidence: bool = False


def build_context(chunks: list[RetrievedChunk], *, excerpt_chars: int, budget_chars: int) -> str:
    """Numbered excerpt blocks under a total character budget."""
    blocks: list[str] = []
    used = 0
    for index, chunk in enumerate(chunks, start=1):
        remaining = budget_chars - used
        if remaining <= 0:
            break
        limit = min(excerpt_chars, remaining)
        excerpt = chunk.text[:limit].rstrip()
        label_bits = [chunk.source_id]
        if chunk.section:
            label_bits.append(chunk.section)
        header = f"[{index}] ({' — '.join(label_bits)})"
        block = f"{header}\n{excerpt}"
        blocks.append(block)
        used += len(block) + 1
    return "\n\n".join(blocks)


def parse_citations(text: str, max_index: int) -> list[int]:
    """Validated, deduplicated citation indices, in order of first appearance."""
    seen: list[int] = []
    for match in _CITATION_RE.finditer(text):
        index = int(match.group(1))
        if 1 <= index <= max_index and index not in seen:
            seen.append(index)
    return seen


class RagChain:
    def __init__(
        self,
        engine,
        store: VectorStore,
        *,
        settings: Settings | None = None,
        tracer: Tracer | None = None,
        rewriter=None,
        pipeline=None,
        system_prompt: str | None = None,
    ):
        self.engine = engine
        self.store = store
        self.settings = settings or get_settings()
        self.tracer = tracer if tracer is not None else NULL_TRACER
        self.rewriter = rewriter
        self.pipeline = pipeline
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT

    def run(
        self,
        question: str,
        *,
        top_k: int | None = None,
        filters: dict | None = None,
        history: list[dict] | None = None,
    ) -> RagAnswer:
        k = top_k or self.settings.rag_top_k
        trace = self.tracer.start_trace("rag_query", input={"question": question[:512]})
        path: list[str] = []

        # 1. Rewrite / normalize.
        rewritten = question
        rewrite_filters: dict = {}
        if self.rewriter is not None:
            with trace.span("rewrite"):
                try:
                    result = self.rewriter.rewrite(question, history=history)
                except TypeError:
                    result = self.rewriter.rewrite(question)
            rewritten = result.rewritten
            rewrite_filters = result.filters
            path.append("rewrite")

        merged_filters = {**rewrite_filters, **(filters or {})}  # caller wins

        # 2. Embed the normalized question.
        with trace.span("embed", model_hint="question"):
            embeddings = self.engine.embed([rewritten])
        vector = embeddings[0].vector
        path.append("embed")

        # 3. Retrieve with metadata filters.
        with trace.span("retrieve", top_k=k, filters=str(merged_filters)):
            chunks = self.store.search(vector, top_k=k, filters=merged_filters)
        path.append("retrieve")

        # 3b. Automated on-demand SEC filing ingestion if no chunks are found
        if not chunks and self.pipeline is not None:
            target_ticker = merged_filters.get("ticker")
            if not target_ticker and self.rewriter is not None:
                detected = getattr(self.rewriter, "_detect_tickers", lambda _: [])(rewritten)
                if detected:
                    target_ticker = detected[0]
            if target_ticker:
                logger.info(
                    "No indexed chunks found for %s; auto-ingesting SEC filings on-demand",
                    target_ticker,
                )
                try:
                    with trace.span("auto_ingest", ticker=target_ticker):
                        self.pipeline.ingest(
                            {"ticker": target_ticker, "filing_type": "10-K", "limit": 1},
                            source_type="sec_filing",
                        )
                    with trace.span("retrieve_after_ingest", top_k=k, filters=str(merged_filters)):
                        chunks = self.store.search(vector, top_k=k, filters=merged_filters)
                    if chunks:
                        path.append("auto_ingest")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Auto-ingestion for %s failed: %s", target_ticker, exc)

        if not chunks:
            output = RagAnswer(
                answer=(
                    "I couldn't find any indexed documents relevant to this "
                    "question. Ingest sources first (POST /ingest), then retry."
                ),
                agent_path=path,
                trace_url=trace.url,
                rewritten_query=rewritten,
                filters_used=merged_filters,
                insufficient_evidence=True,
            )
            trace.finish(output={"status": "no_retrieved_chunks"})
            return output

        # 4. Grounded context + generation.
        context = build_context(
            chunks,
            excerpt_chars=self.settings.rag_excerpt_chars,
            budget_chars=self.settings.rag_context_char_budget,
        )
        history_context = ""
        if history:
            recent_turns = []
            for turn in history[-4:]:
                r = (
                    "User"
                    if (turn.get("role") if isinstance(turn, dict) else getattr(turn, "role", ""))
                    == "user"
                    else "Assistant"
                )
                t = (
                    turn.get("content", "")
                    if isinstance(turn, dict)
                    else getattr(turn, "content", "")
                )
                if t:
                    recent_turns.append(f"{r}: {t[:300]}")
            if recent_turns:
                history_context = (
                    "Previous conversation context:\n" + "\n".join(recent_turns) + "\n\n"
                )

        prompt = f"{history_context}Question: {rewritten}\n\nExcerpts:\n{context}\n\nAnswer:"
        try:
            with trace.span("generate", provider=self._generation_provider()):
                response: GenerationResult = self.engine.generate(
                    prompt, system=self.system_prompt, temperature=0.1
                )
        except ProviderUnavailableError:
            trace.finish(output={"status": "generation_unavailable"})
            raise
        path.append("generate")

        answer_text = response.text.strip()
        insufficient = answer_text.startswith(INSUFFICIENT_MARKER)
        if insufficient:
            note = _insufficiency_note(answer_text)
            answer_text = (
                "I don't have enough evidence in the indexed sources to answer "
                "this question reliably." + (f" {note}" if note else "")
            )

        cited = parse_citations(response.text, len(chunks))
        citations = [_citation_for(chunks[index - 1]) for index in cited]
        if insufficient:
            citations = []

        output_meta = {
            "status": "insufficient_evidence" if insufficient else "answered",
            "citations": len(citations),
        }
        trace.finish(output=output_meta)
        return RagAnswer(
            answer=answer_text,
            citations=citations,
            agent_path=path,
            trace_url=trace.url,
            retrieved_chunks=chunks,
            rewritten_query=rewritten,
            filters_used=merged_filters,
            insufficient_evidence=insufficient,
        )

    def _generation_provider(self) -> str | None:
        getter = getattr(self.engine, "available_providers", None)
        available: list[str] = list(getter()) if callable(getter) else []
        return available[0] if available else None


def _citation_for(chunk: RetrievedChunk) -> dict:
    return {
        "source_id": chunk.source_id,
        "title": chunk.metadata.get("title") or chunk.source_id,
        "excerpt": chunk.text[:_EXCERPT_CHARS_IN_CITATION].rstrip(),
        "url": chunk.metadata.get("url"),
        "chunk_id": chunk.chunk_id,
        "score": round(chunk.score, 4),
        "section": chunk.section,
        "page_or_position": chunk.page_or_position,
    }


def _insufficiency_note(raw_answer: str) -> str:
    """The model's own explanation after the marker, cleaned for display."""
    remainder = raw_answer[len(INSUFFICIENT_MARKER) :].lstrip(" :—-\n")
    remainder = re.sub(r"\s+", " ", remainder).strip()
    if not remainder:
        return ""
    return remainder[0].upper() + remainder[1:]
