"""Fetch agent (spec section 10.2) — evidence gathering with loop protection.

Order of operations, per the "indexed chunks first" requirement:

1. Plan deterministically from the question: tickers, date range, source
   types (regex/entity-extractor based, zero LLM cost).
2. Search what is ALREADY indexed in the vector store for those dimensions.
3. Only for (ticker, source_type) combinations with zero hits, trigger live
   ingestion through the pipeline — bounded by `max_live_ingests` per query
   and the state's `ingested_keys` memory, so a query can never cause
   repeated ingestion loops.
4. Merge SEC + news evidence, deduplicated by chunk id, scores preserved.

Unavailable sources (missing key, disabled adapter, failed live ingest) are
reported explicitly via state["unavailable_sources"] instead of failing the
node: partial evidence still supports a grounded answer downstream.

The node function signature matches LangGraph convention: takes AgentState,
returns a partial-update dict.
"""

import logging
import re
from dataclasses import dataclass, field

from config.settings import Settings, get_settings
from ingestion.entity_extractor import BUILTIN_TICKERS, COMPANY_ALIASES
from models.schemas import RetrievedChunk
from observability.langfuse_wrapper import NULL_TRACER, Tracer
from retrieval.base import VectorStore

logger = logging.getLogger(__name__)

# Words that make a news feed relevant; filings stay the default evidence base.
_NEWS_HINT_RE = re.compile(
    r"\b(news|announced|announcement|report(ed|edly)?|headline|press release|"
    r"earnings call|guidance|upgraded|downgraded|lawsuit|recall|merger|acquisition)\b",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")  # non-capturing: findall needs full years
_CASHTAG_RE = re.compile(r"\$([A-Za-z]{2,5})\b")
_TOKEN_RE = re.compile(r"\b([A-Z][A-Z0-9.&/-]{0,6})\b")

_MAX_CHUNKS_FOR_ANSWER = 24  # total evidence ceiling handed to extract/synthesize


@dataclass
class QueryPlan:
    """What the fetch agent will look for. Deterministic output."""

    retrieval_query: str
    tickers: list[str] = field(default_factory=list)
    date_range: tuple[str | None, str | None] | None = None
    source_types: list[str] = field(default_factory=lambda: ["sec_filing", "news"])


class QueryPlanner:
    """Regex-level entity/date/source planning shared by tests and the agent."""

    def __init__(self, *, known_tickers: frozenset[str] | set[str] | None = None):
        self.known_tickers = (
            frozenset(known_tickers) if known_tickers is not None else BUILTIN_TICKERS
        )

    def detect_tickers(self, text: str) -> list[str]:
        found: list[str] = [m.group(1).upper() for m in _CASHTAG_RE.finditer(text)]
        lower_text = text.lower()
        for company_name, ticker in COMPANY_ALIASES.items():
            if re.search(rf"\b{re.escape(company_name)}\b", lower_text):
                found.append(ticker)

        alternation = "|".join(
            re.escape(t) for t in sorted(self.known_tickers, key=len, reverse=True) if t.isalpha()
        )
        if alternation:
            found.extend(m.group(0) for m in re.finditer(rf"\b({alternation})\b", text))
        ordered: list[str] = []
        for ticker in found:
            if ticker not in ordered:
                ordered.append(ticker)
        return ordered

    def plan(self, question: str, history: list[dict] | None = None) -> QueryPlan:
        cleaned = re.sub(r"\s+", " ", question).strip()
        tickers = self.detect_tickers(cleaned)

        # Context-aware follow-up resolution:
        if history:
            prev_user_queries = [
                turn.get("content", "")
                if isinstance(turn, dict)
                else getattr(turn, "content", "")
                for turn in history
                if (
                    turn.get("role") if isinstance(turn, dict) else getattr(turn, "role", "")
                )
                == "user"
            ]
            last_q = prev_user_queries[-1].strip() if prev_user_queries else ""
            cleaned_lower = cleaned.lower()
            action_commands = {
                "do it",
                "do that",
                "do",
                "go ahead",
                "summarize",
                "summarize it",
                "expand",
                "expand it",
                "tell me",
                "yes",
                "please do",
                "continue",
                "what are they",
                "list them",
                "explain",
                "give details",
                "elaborate",
            }
            if cleaned_lower in action_commands and last_q:
                cleaned = last_q
                tickers = self.detect_tickers(cleaned)
            elif not tickers:
                for turn in reversed(history):
                    content = (
                        turn.get("content", "")
                        if isinstance(turn, dict)
                        else getattr(turn, "content", "")
                    )
                    prev_tickers = self.detect_tickers(content)
                    if prev_tickers:
                        tickers = prev_tickers
                        words = cleaned_lower.split()
                        if len(words) <= 6 or any(
                            w in words
                            for w in (
                                "it",
                                "its",
                                "their",
                                "that",
                                "this",
                                "do",
                                "how",
                                "what",
                                "and",
                                "why",
                                "more",
                            )
                        ):
                            cleaned = f"{tickers[0]} {cleaned}"
                        # Inherit fiscal year context if missing in current follow-up query
                        prev_years = _YEAR_RE.findall(content)
                        cur_years = _YEAR_RE.findall(cleaned)
                        if prev_years and not cur_years:
                            cleaned = f"{cleaned} fiscal {prev_years[-1]}"
                        break

        years = sorted({int(y) for y in _YEAR_RE.findall(cleaned)})
        date_range = None
        if years:
            date_range = (f"{years[0]}-01-01", f"{years[-1]}-12-31")

        source_types = ["sec_filing"]
        if _NEWS_HINT_RE.search(cleaned):
            source_types.append("news")

        return QueryPlan(
            retrieval_query=cleaned or question.strip(),
            tickers=tickers,
            date_range=date_range,
            source_types=source_types,
        )


class FetchAgent:
    """Agent node: retrieve evidence from vector store, live-ingesting if needed."""

    name = "fetch"

    def __init__(
        self,
        *,
        engine,
        store: VectorStore,
        adapters: dict,
        pipeline=None,
        settings: Settings | None = None,
        tracer: Tracer | None = None,
        planner: QueryPlanner | None = None,
        top_k_per_search: int = 8,
        max_live_ingests: int = 2,
    ):
        self.engine = engine
        self.store = store
        self.adapters = adapters  # keyed by source_type ("sec_filing", "news")
        self.pipeline = pipeline  # IngestionPipeline or None (no live fetching)
        self.settings = settings or get_settings()
        self.tracer = tracer if tracer is not None else NULL_TRACER
        self.planner = planner or QueryPlanner()
        self.top_k_per_search = top_k_per_search
        self.max_live_ingests = max_live_ingests

    def __call__(self, state: dict) -> dict:
        trace = self.tracer.start_trace(
            "agent_fetch", input={"query": state.get("query", "")[:512]}
        )
        plan = self.planner.plan(state.get("query", ""), history=state.get("history"))
        updates: dict = {
            "tickers": plan.tickers,
            "agent_path": [*state.get("agent_path", []), self.name],
        }

        low = plan.retrieval_query.lower()
        sec_additions: list[str] = []
        if "risk" in low and "item 1a" not in low:
            sec_additions.append("Item 1A Risk Factors")
        if any(m in low for m in ["md&a", "management discussion", "results of operation"]) and "item 7" not in low:
            sec_additions.append("Item 7 MD&A")
        if any(m in low for m in ["balance sheet", "cash flow", "financial statement", "profit", "net income", "margin"]) and "item 8" not in low:
            sec_additions.append("Item 8 Financial Statements")
        if any(w in low for w in ["profit", "net income", "earnings", "income", "margin"]):
            sec_additions.append("net income gross margin operating income")
        if any(w in low for w in ["revenue", "sales", "net sales"]):
            sec_additions.append("total net sales revenue")
        embed_query = f"{plan.retrieval_query} {' '.join(sec_additions)}" if sec_additions else plan.retrieval_query

        try:
            vector = self.engine.embed([embed_query])[0].vector
        except Exception as exc:  # noqa: BLE001 — degrade to empty evidence
            logger.warning("Fetch embed failed (%s); continuing without evidence", exc)
            trace.finish(output={"status": "embed_failed"})
            return {
                **updates,
                "retrieved_chunks": [],
                "limitations": ["retrieval unavailable (embedding provider failed)"],
            }
        updates.setdefault("trace_urls", []).append(trace.url)

        ticker_filter = plan.tickers[0] if len(plan.tickers) == 1 else None
        filters_base: dict = {}
        if ticker_filter:
            filters_base["ticker"] = ticker_filter
        if plan.date_range:
            filters_base["date_range"] = list(plan.date_range)

        retrieved: dict[str, RetrievedChunk] = {}
        for source_type in plan.source_types:
            found = self._search(vector, {**filters_base, "source_type": source_type})
            for chunk in found:
                retrieved[chunk.chunk_id] = chunk
            if len(plan.tickers) > 1:
                for ticker in plan.tickers[:2]:
                    for chunk in self._search(vector, {"ticker": ticker, "source_type": source_type}):
                        retrieved[chunk.chunk_id] = chunk

        # Live-ingest only the empty (ticker, source_type) combos, bounded.
        ingested_keys = list(state.get("ingested_keys", []))
        unavailable = list(state.get("unavailable_sources", []))
        limitations = list(state.get("limitations", []))
        live_budget = self.max_live_ingests

        for source_type in plan.source_types:
            adapter = self.adapters.get(source_type)
            adapter_name = getattr(adapter, "name", source_type)
            if adapter is None or not adapter.is_available():
                reason = "not configured" if adapter is None else "unavailable (missing API key)"
                unavailable.append(f"{adapter_name}: {reason}")
                continue
            for ticker in plan.tickers[:2]:  # cap live work per source type
                if source_type == "sec_filing":
                    ticker_has_chunks = any(
                        c.source_type == source_type
                        and (
                            ":10-K:" in (c.source_id or "")
                            or ":10-Q:" in (c.source_id or "")
                            or c.metadata.get("form") in ("10-K", "10-Q")
                        )
                        and (
                            getattr(c, "ticker", None) == ticker
                            or c.metadata.get("ticker") == ticker
                            or (c.source_id and f":{ticker}:" in c.source_id)
                        )
                        for c in retrieved.values()
                    )
                else:
                    ticker_has_chunks = any(
                        c.source_type == source_type
                        and (
                            getattr(c, "ticker", None) == ticker
                            or c.metadata.get("ticker") == ticker
                            or (c.source_id and f":{ticker}:" in c.source_id)
                            or (ticker in c.entities)
                        )
                        for c in retrieved.values()
                    )
                if ticker_has_chunks:
                    continue
                key = f"{ticker}:{source_type}"
                if key in ingested_keys:
                    continue
                if live_budget <= 0:
                    limitations.append(f"live ingestion skipped for {key} (budget)")
                    continue
                live_budget -= 1
                ingested_keys.append(key)  # recorded BEFORE attempting: one try per run
                stats = self._ingest(adapter, source_type, ticker, plan, unavailable)
                if stats:
                    for chunk in self._search(vector, {**filters_base, "source_type": source_type}):
                        retrieved[chunk.chunk_id] = chunk
                    for chunk in self._search(vector, {"ticker": ticker, "source_type": source_type}):
                        retrieved[chunk.chunk_id] = chunk

        ordered = sorted(retrieved.values(), key=lambda c: (-c.score, c.chunk_id))
        truncated = len(ordered) > _MAX_CHUNKS_FOR_ANSWER
        ordered = ordered[:_MAX_CHUNKS_FOR_ANSWER]

        trace.finish(
            output={
                "status": "ok" if ordered else "empty",
                "chunks": len(ordered),
                "live_ingests": len(ingested_keys) - len(state.get("ingested_keys", [])),
                "truncated": truncated,
            }
        )
        return {
            **updates,
            "retrieved_chunks": ordered,
            "ingested_keys": ingested_keys,
            "unavailable_sources": unavailable,
            "limitations": limitations,
        }

    def _search(self, vector: list[float], filters: dict) -> list[RetrievedChunk]:
        try:
            raw = self.store.search(
                vector, top_k=max(self.top_k_per_search * 5, 48), filters=filters
            )
            _footer_re = re.compile(r"Form\s+10-[KQ]\s*\|\s*\d+", re.IGNORECASE)
            _toc_re = re.compile(r"\|\s*Item\s+\d+.*\|\s*\d+\s*\|", re.IGNORECASE)
            _preamble_re = re.compile(
                r"(?:consolidated\s+(?:statements|balance\s+sheets)|the\s+following\s+table|were\s+as\s+follows|was\s+as\s+follows|as\s+follows\b)",
                re.IGNORECASE,
            )
            substantive = [
                c
                for c in raw
                if not (_footer_re.search(c.text) and len(c.text.strip()) < 120)
                and not (_toc_re.search(c.text) and len(c.text.strip()) < 600)
                and not (_preamble_re.search(c.text.strip()) and "|" not in c.text and len(c.text.strip()) < 350)
            ]
            return (substantive if substantive else raw)[: self.top_k_per_search]
        except Exception as exc:  # noqa: BLE001 — a broken store must not kill the node
            logger.warning("Store search failed for %s (%s)", filters, exc)
            return []

    def _ingest(
        self, adapter, source_type: str, ticker: str, plan: QueryPlan, unavailable: list[str]
    ) -> bool:
        """Bounded live ingestion. Returns True when it may have added content."""
        if self.pipeline is None:
            unavailable.append(f"{adapter.name}: live ingestion not wired")
            return False
        params: dict = {
            "ticker": ticker,
            "limit": 5 if source_type == "news" else 1,
            "filing_type": "10-K",
        }
        if plan.date_range:
            params["date_range"] = list(plan.date_range)
        try:
            stats = self.pipeline.ingest(params, source_type=source_type)
        except Exception as exc:  # noqa: BLE001 — report, never crash the node
            logger.warning("Live ingestion failed for %s (%s)", f"{ticker}:{source_type}", exc)
            unavailable.append(f"{adapter.name}: live ingestion failed ({type(exc).__name__})")
            return False
        logger.info(
            "Live ingest %s: fetched=%d indexed=%d failed=%d",
            f"{ticker}:{source_type}",
            stats.documents_fetched,
            stats.chunks_indexed,
            stats.documents_failed,
        )
        return stats.chunks_indexed > 0


__all__ = ["FetchAgent", "QueryPlan", "QueryPlanner"]
