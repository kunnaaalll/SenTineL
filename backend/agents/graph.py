"""Agent graph + query routing (spec section 10).

Two responsibilities live here:

1. Routing (deterministic, no LLM): classify_query() decides simple vs
   multi_hop from ticker count, comparison vocabulary, and period mentions.
   The existing RagChain stays the entire simple path; the LangGraph team
   serves multi-hop questions and the forced /agents/query route.

2. The multi-hop graph: fetch -> extract -> (compare?) -> synthesize over the
   shared AgentState. Nodes are plain callables taking state and returning
   partial updates; each is independently callable for tests.

Failure handling: every node runs under _guarded() with one bounded retry;
after that a per-node degrade function returns useful partial state instead
of raising, so partial failures still produce a grounded response. The
topology is acyclic and invoke() carries a recursion_limit backstop — there
is no cycle for retries to spin in, and fetch's ingestion budget prevents
repeated-ingestion loops (see FetchAgent).

Observable: agents emit their own traces through the injected Tracer
abstraction; the service surfaces the first non-null trace URL.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from langgraph.graph import END, START, StateGraph

from agents.compare_agent import CompareAgent, comparison_warranted
from agents.extract_agent import ExtractAgent, deterministic_facts
from agents.fetch_agent import FetchAgent, QueryPlanner
from agents.state import AgentState, ExtractedFact, initial_state
from agents.synthesize_agent import REFUSAL_PREFIX, SynthesizeAgent
from chains.rag_chain import RagAnswer, RagChain
from config.settings import Settings, get_settings
from observability.langfuse_wrapper import NULL_TRACER, Tracer

logger = logging.getLogger(__name__)

_COMPARISON_HINT_RE = re.compile(
    r"\b(compare|compared|comparison|comparing|versus|vs\.?|against|relative to|"
    r"difference between|year[- ]over[- ]year|over time|across)\b",
    re.IGNORECASE,
)
_PERIOD_TOKEN_RE = re.compile(r"\b(?:FY\s?'?\d{2,4}|Q[1-4]\s?(?:FY'?\s?\d{4})?|(?:19|20)\d{2})\b")


def classify_query(question: str, *, planner: QueryPlanner | None = None) -> str:
    """Deterministic simple/multi_hop decision (spec section 10.1).

    multi_hop when: 2+ tickers, comparison vocabulary, or 2+ distinct
    period/year tokens. Everything else rides the simple RAG path."""
    planner = planner or QueryPlanner()
    if len(planner.detect_tickers(question)) >= 2:
        return "multi_hop"
    if _COMPARISON_HINT_RE.search(question):
        return "multi_hop"
    periods = {
        re.sub(r"[^0-9A-Z]", "", token.upper()) for token in _PERIOD_TOKEN_RE.findall(question)
    }
    years = {re.sub(r"\D", "", p) for p in periods if re.sub(r"\D", "", p)}
    if len(years) >= 2 or len(periods) >= 2:
        return "multi_hop"
    return "simple"


# --------------------------------------------------------------------------
# Guarded execution: bounded retry then degrade
# --------------------------------------------------------------------------

RetryableFn = Callable[[dict], dict]
DegradeFn = Callable[[dict, Exception], dict]


def _guarded(node_name: str, fn: RetryableFn, degrade: DegradeFn, *, retries: int = 1):
    """Run a node with bounded retries; on exhaustion record the failure and
    apply the node-specific degrade. Never raises."""

    def wrapped(state: dict) -> dict:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return fn(state)
            except Exception as exc:  # noqa: BLE001 — degradation contract
                last_error = exc
                logger.warning(
                    "Node %s failed (attempt %d/%d): %s",
                    node_name,
                    attempt + 1,
                    retries + 1,
                    exc,
                )
        # Exception TYPE only: messages can embed prompts/provider payloads,
        # and node_errors must never become a leak channel if ever surfaced.
        error = {
            "node": node_name,
            "error": type(last_error).__name__,
            "recovered": True,
        }
        updates = dict(degrade(state, last_error))  # type: ignore[arg-type]
        errors = [*state.get("node_errors", []), *updates.pop("node_errors", []), error]
        return {**updates, "node_errors": errors}

    return wrapped


def _degrade_fetch(_state: dict, _exc: Exception) -> dict:
    return {"retrieved_chunks": [], "limitations": ["evidence retrieval failed"]}


def _degrade_extract(state: dict, _exc: Exception) -> dict:
    facts = []
    for chunk in (state.get("retrieved_chunks") or [])[:12]:
        facts.extend(deterministic_facts(chunk))
    return {"extracted_facts": [fact.model_dump() for fact in facts]}


def _degrade_compare(_state: dict, _exc: Exception) -> dict:
    return {"comparison_table": None}


def _degrade_synthesize(_state: dict, _exc: Exception) -> dict:
    # final_answer=None lets the service fall back to its deterministic digest.
    return {}


def route_after_extract(state: dict) -> str:
    """Conditional edge: compare only when facts actually span entities/periods."""
    try:
        facts = [ExtractedFact(**fact) for fact in state.get("extracted_facts", [])]
    except Exception:  # noqa: BLE001 — malformed facts skip comparison entirely
        return "synthesize"
    return "compare" if comparison_warranted(facts) else "synthesize"


def build_agent_graph(fetch_agent, extract_agent, compare_agent, synthesize_agent):
    """Compile fetch -> extract -> (compare?) -> synthesize. Acyclic by design."""
    builder: StateGraph = StateGraph(AgentState)
    builder.add_node("fetch", _guarded("fetch", fetch_agent, _degrade_fetch))
    builder.add_node("extract", _guarded("extract", extract_agent, _degrade_extract))
    builder.add_node("compare", _guarded("compare", compare_agent, _degrade_compare))
    builder.add_node("synthesize", _guarded("synthesize", synthesize_agent, _degrade_synthesize))
    builder.add_edge(START, "fetch")
    builder.add_edge("fetch", "extract")
    builder.add_conditional_edges(
        "extract",
        route_after_extract,
        {"compare": "compare", "synthesize": "synthesize"},
    )
    builder.add_edge("compare", "synthesize")
    builder.add_edge("synthesize", END)
    return builder.compile()


# --------------------------------------------------------------------------
# Orchestration service used by both API routes
# --------------------------------------------------------------------------


@dataclass
class AnswerResult:
    """Everything /query and /agents/query need to build a QueryResponse."""

    answer: str
    citations: list[dict] = field(default_factory=list)
    agent_path: list[str] = field(default_factory=list)
    trace_url: str | None = None
    query_type: str = "simple"
    insufficient_evidence: bool = False


def _digest_answer(state: dict) -> tuple[str, bool]:
    """Last-resort deterministic answer from whatever survived the run."""
    facts = [ExtractedFact(**fact) for fact in state.get("extracted_facts", [])]
    chunks = state.get("retrieved_chunks") or []
    question = state.get("query", "")
    unavailable = state.get("unavailable_sources", [])

    if not facts and not chunks:
        reason = f" Unavailable: {'; '.join(unavailable)}." if unavailable else ""
        return (
            f'{REFUSAL_PREFIX} to answer "{question}" — the agent run '
            f"produced no usable evidence.{reason}"
        ), True

    lines = [
        f"Full synthesis was interrupted, so here is the verified evidence "
        f'collected for "{question}":'
    ]
    for fact in facts[:10]:
        label = fact.entity + (f" {fact.metric}" if fact.metric else "")
        value = fact.value or fact.statement or ""
        period = f" ({fact.period})" if fact.period else ""
        lines.append(f"- {label}{period}: {value}")
    if unavailable:
        lines.append("Unavailable sources: " + "; ".join(unavailable))
    return "\n".join(lines), False


class SentinelQueryService:
    """Single entry point behind POST /query and POST /agents/query."""

    def __init__(
        self,
        *,
        rag_chain: RagChain,
        fetch_agent: FetchAgent,
        extract_agent: ExtractAgent,
        compare_agent: CompareAgent,
        synthesize_agent: SynthesizeAgent,
        settings: Settings | None = None,
        tracer: Tracer | None = None,
    ):
        self.rag_chain = rag_chain
        self.fetch_agent = fetch_agent
        self.extract_agent = extract_agent
        self.compare_agent = compare_agent
        self.synthesize_agent = synthesize_agent
        self.settings = settings or get_settings()
        self.tracer = tracer if tracer is not None else NULL_TRACER
        self.planner = QueryPlanner()
        self.graph = build_agent_graph(fetch_agent, extract_agent, compare_agent, synthesize_agent)

    def classify(self, question: str) -> str:
        return classify_query(question, planner=self.planner)

    def answer(
        self,
        question: str,
        *,
        force_agents: bool = False,
        top_k: int | None = None,
        filters: dict | None = None,
        history: list[dict] | None = None,
    ) -> AnswerResult:
        query_type = "multi_hop" if force_agents else self.classify(question)

        if query_type == "simple":
            try:
                rag_result: RagAnswer = self.rag_chain.run(
                    question, top_k=top_k, filters=filters, history=history
                )
            except TypeError:
                rag_result = self.rag_chain.run(question, top_k=top_k, filters=filters)
            return AnswerResult(
                answer=rag_result.answer,
                citations=rag_result.citations,
                agent_path=["classify", *rag_result.agent_path],
                trace_url=rag_result.trace_url,
                query_type="simple",
                insufficient_evidence=rag_result.insufficient_evidence,
            )

        state = initial_state(
            question, force_agents=True, query_type="multi_hop", history=history
        )
        trace = self.tracer.start_trace(
            "agents_query", input={"question": question[:512], "forced": force_agents}
        )
        try:
            final: dict = self.graph.invoke(state, config={"recursion_limit": 25})
        except Exception as exc:  # noqa: BLE001 — framework-level safety net
            logger.exception("Agent graph failed outright")
            final = {
                **state,
                "node_errors": [{"node": "graph", "error": type(exc).__name__, "recovered": True}],
            }
        trace.finish(
            output={
                "agent_path": final.get("agent_path", []),
                "node_errors": len(final.get("node_errors", [])),
                "chunks": len(final.get("retrieved_chunks", []) or []),
                "facts": len(final.get("extracted_facts", []) or []),
            }
        )

        answer_text = final.get("final_answer")
        insufficient = False
        if not answer_text:
            answer_text, insufficient = _digest_answer(final)
        elif answer_text.startswith(REFUSAL_PREFIX):
            insufficient = True

        citations = final.get("citations") or []
        trace_urls = [url for url in final.get("trace_urls", []) if url]
        return AnswerResult(
            answer=answer_text,
            citations=citations,
            # initial_state already seeded "classify"; nodes append their names.
            agent_path=list(final.get("agent_path", [])),
            trace_url=trace_urls[0] if trace_urls else trace.url,
            query_type="multi_hop",
            insufficient_evidence=insufficient,
        )


__all__ = [
    "AnswerResult",
    "SentinelQueryService",
    "build_agent_graph",
    "classify_query",
]
