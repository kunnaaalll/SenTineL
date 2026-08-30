"""Synthesize agent (spec section 10.2) — grounded, cited final answers.

Inputs: retrieved chunks (numbered excerpts), extracted facts, the comparison
table when present, plus every limitation/unavailable-source note accumulated
upstream. The LLM sees all of it and is instructed to cite inline with [n];
citations are then VALIDATED against the real chunk set exactly like the
simple RAG chain does — markers that don't resolve to a chunk are dropped, so
an invented citation can never survive into QueryResponse.

Degradation ladder (a useful grounded response over an exception):
1. Normal path: generated answer + validated citations.
2. INSUFFICIENT_EVIDENCE marker: refusal text + notes on what's missing.
3. Provider failure: deterministic digest answer built directly from facts,
   each bullet citing its fact's chunk; unavailable sources called out.
4. Nothing at all (no chunks, no facts): explicit inability statement that
   names which sources were unavailable — still a 200-shaped grounded reply.

Limitations (gaps flagged by fetch/extract/compare) are appended as a
"Limitations:" block so stale/conflicting/missing evidence is always visible
in the final answer.
"""

import logging
from typing import Any, cast

from agents.state import AgentState, ExtractedFact, unique_chunks
from chains.rag_chain import (
    INSUFFICIENT_MARKER,
    _citation_for,
    build_context,
    parse_citations,
)
from config.settings import Settings, get_settings
from llm_providers.base import ProviderError
from models.schemas import RetrievedChunk
from observability.langfuse_wrapper import NULL_TRACER, Tracer

logger = logging.getLogger(__name__)

REFUSAL_PREFIX = "I couldn't gather enough indexed evidence"

DEFAULT_SYSTEM_PROMPT = f"""You are Sentinel, a financial research analyst writing the \
final answer for a multi-step investigation. Use the numbered evidence \
excerpts and the structured facts provided.

Rules:
1. Synthesize and answer the user's question directly using the metrics, figures, and facts in the provided excerpts and extracted facts. Cite supporting excerpts inline with their numbers in brackets — e.g. [1] or [2][3] — for every factual claim.
2. Never invent metrics, dates, companies, figures, or citations.
3. When comparing companies or periods, present whatever data is available for each entity (such as segment revenue, cloud growth rates, operating metrics) and clearly note any gaps or non-comparable definitions.
4. If the supplied comparison table flags cells as missing or conflicting, present the available data and note the missing items explicitly.
5. When notable gaps exist, end with a short "Limitations:" paragraph.
6. Begin your reply with {INSUFFICIENT_MARKER} ONLY if there is zero usable financial data, zero extracted facts, and zero relevant excerpts for the requested topic.
7. Output ONLY the final direct, polished response for the user. Never include internal monologue, chain-of-thought scratchpads, self-corrections, or meta-commentary."""

_PROMPT_TEMPLATE = """Question: {question}

Extracted facts:
{facts}

{comparison}

Evidence excerpts:
{context}

{notes}

Final cited answer:"""


def _fact_line(fact: ExtractedFact) -> str:
    bits = [fact.entity]
    if fact.metric:
        bits.append(fact.metric)
    if fact.period:
        bits.append(f"({fact.period})")
    head = " ".join(bits)
    body = fact.value or fact.statement or ""
    qualifiers: list[str] = []
    if fact.kind != "reported":
        qualifiers.append(fact.kind)
    if fact.confidence < 0.35:
        qualifiers.append("low confidence")
    suffix = f" [{'; '.join(qualifiers)}]" if qualifiers else ""
    return f"- {head}: {body}{suffix}"


def _comparison_lines(table: dict | None) -> str:
    if not table or not table.get("warranted"):
        return "(no cross-entity/period comparison was warranted)"
    lines: list[str] = []
    for row in table.get("rows", []):
        header = f"{row['metric']} @ {row['period']}"
        cells = "; ".join(
            f"{cell['entity']}={cell['value'] if cell['value'] is not None else 'MISSING'}"
            f"{' [conflict]' if cell['status'] == 'conflict' else ''}"
            for cell in row.get("cells", [])
        )
        line = f"- {header}: {cells}"
        if row.get("note"):
            line += f" — note: {row['note']}"
        lines.append(line)
    return "\n".join(lines) if lines else "(comparison produced no rows)"


def _notes_block(unavailable: list[str], limitations: list[str]) -> str:
    parts: list[str] = []
    if unavailable:
        parts.append("Unavailable sources: " + "; ".join(unavailable))
    if limitations:
        parts.append("Known issues: " + "; ".join(limitations))
    return "\n".join(parts)


class SynthesizeAgent:
    name = "synthesize"

    def __init__(
        self,
        *,
        engine,
        settings: Settings | None = None,
        tracer: Tracer | None = None,
        system_prompt: str | None = None,
    ):
        self.engine = engine
        self.settings = settings or get_settings()
        self.tracer = tracer if tracer is not None else NULL_TRACER
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT

    def __call__(self, state: AgentState) -> dict:
        question = state.get("query", "")
        chunks = unique_chunks(state.get("retrieved_chunks") or [])
        facts: list[ExtractedFact] = []
        incoming: list[Any] = cast("list[Any]", state.get("extracted_facts", []))
        for fact in incoming:
            # Graph traffic arrives as dicts; tolerate direct callers passing
            # validated models so the node stays independently callable.
            facts.append(fact if isinstance(fact, ExtractedFact) else ExtractedFact(**fact))
        unavailable = list(state.get("unavailable_sources", []))
        limitations = list(state.get("limitations", []))

        trace = self.tracer.start_trace(
            "agent_synthesize",
            input={"chunks": len(chunks), "facts": len(facts)},
        )

        generated: str | None = None
        if chunks or facts:
            # Nothing to ground on -> don't spend a call; the fallback below
            # refuses explicitly instead of letting the model freestyle.
            context = build_context(
                chunks,
                excerpt_chars=self.settings.rag_excerpt_chars,
                budget_chars=self.settings.rag_context_char_budget,
            )
            prompt = _PROMPT_TEMPLATE.format(
                question=question,
                facts="\n".join(_fact_line(fact) for fact in facts) or "(none)",
                comparison=_comparison_lines(state.get("comparison_table")),
                context=context or "(none)",
                notes=_notes_block(unavailable, limitations) or "(none)",
            )
            try:
                response = self.engine.generate(prompt, system=self.system_prompt, temperature=0.2)
                generated = response.text.strip()
            except ProviderError as exc:
                logger.warning("Generation unavailable for synthesis (%s); using digest", exc)

        citations: list[dict] = []
        if generated is not None and generated.startswith(INSUFFICIENT_MARKER):
            # Explicit, honest refusal — never dress missing evidence up as an
            # answer (same discipline as the simple RAG chain).
            answer = f'I don\'t have enough indexed evidence to answer "{question}" reliably.'
            status = "insufficient_evidence"
        elif generated is not None:
            answer = generated
            if chunks:
                indices = parse_citations(generated, len(chunks))
                citations = [_citation_for(chunks[index - 1]) for index in indices]
            status = "answered"
        else:
            answer, citations = self._fallback_answer(question, facts, chunks, unavailable)
            status = "digest" if (chunks or facts) else "insufficient_evidence"

        closing = _limitations_paragraph(answer, unavailable, limitations)
        if closing:
            answer = f"{answer}\n\n{closing}"

        trace.finish(output={"status": status, "citations": len(citations)})
        return {
            "final_answer": answer,
            "citations": citations,
            "agent_path": [*state.get("agent_path", []), self.name],
            "trace_urls": [*state.get("trace_urls", []), trace.url],
        }

    # -- degraded paths -------------------------------------------------------

    def _fallback_answer(
        self,
        question: str,
        facts: list[ExtractedFact],
        chunks: list[RetrievedChunk],
        unavailable: list[str],
    ) -> tuple[str, list[dict]]:
        """Deterministic digest of the extracted facts with real citations."""
        if not facts and not chunks:
            reason = f" {unavailable[0]}." if unavailable else ""
            return (
                f'{REFUSAL_PREFIX} to answer "{question}" — no usable evidence '
                f"was retrieved.{reason}"
            ), []

        citation_by_chunk = {chunk.chunk_id: _citation_for(chunk) for chunk in chunks}
        used: dict[str, dict] = {}
        lines: list[str] = [
            f"Generated synthesis is unavailable, so here is a direct digest of "
            f'the extracted evidence for "{question}":'
        ]
        for fact in facts[:10]:
            qualifier = "" if fact.kind == "reported" else f" [{fact.kind}]"
            value = fact.value or fact.statement or "unquantified claim"
            label = f"{fact.entity}" + (f" {fact.metric}" if fact.metric else "")
            period = f" ({fact.period})" if fact.period else ""
            marker = ""
            citation = citation_by_chunk.get(fact.source_chunk_id)
            if citation:
                index = len(used) + 1
                used[fact.source_chunk_id] = {**citation}
                marker = f" [{index}]"
            lines.append(f"- {label}{period}: {value}{qualifier}{marker}")

        if unavailable:
            lines.append(
                "Note: some sources were unavailable and may change this picture: "
                + "; ".join(unavailable)
            )
        return "\n".join(lines), list(used.values())


def _limitations_paragraph(
    answer: str, unavailable: list[str], limitations: list[str]
) -> str | None:
    """Closing Limitations block, deduplicated against what's already said."""
    items: list[str] = []
    for note in unavailable:
        if note not in answer:
            items.append(note)
    for note in limitations:
        if note not in answer and note not in items:
            items.append(note)
    if not items:
        return None
    return "Limitations: " + "; ".join(items) + "."


__all__ = ["REFUSAL_PREFIX", "DEFAULT_SYSTEM_PROMPT", "SynthesizeAgent"]
