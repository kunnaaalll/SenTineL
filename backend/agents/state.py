"""Shared agent-team contracts (spec sections 5 and 10.3).

AgentState is the TypedDict threaded through every LangGraph node. Nodes read
specific keys and RETURN PARTIAL UPDATES (only the keys they changed);
LangGraph merges them onto the state. Spec section 5 fixes eight keys; the
Phase 3 operational extensions below are additive and documented here.

ExtractedFact is the strict Pydantic contract produced by the extract agent —
machine-comparable input for compare/synthesize. Provenance is non-negotiable:
every fact carries the chunk id it came from, and extract_agent forces that
value server-side (the model never supplies provenance).
"""

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from models.schemas import RetrievedChunk

QueryType = Literal["simple", "multi_hop"]
ClaimKind = Literal["reported", "estimate", "guidance", "qualitative"]

VALID_CLAIM_KINDS: tuple[str, ...] = ("reported", "estimate", "guidance", "qualitative")


class ExtractedFact(BaseModel):
    """One structured fact extracted from one retrieved chunk."""

    model_config = ConfigDict(extra="forbid")

    entity: str  # ticker or company name the fact is about
    metric: str | None = None  # canonical-ish label, e.g. "total net sales"
    value: str | None = None  # EXACT string as reported — never rounded by us
    numeric_value: float | None = None  # conservative parse of value; None if ambiguous
    unit: str | None = None  # "%", "USD", "million USD", ...
    period: str | None = None  # FY2024, Q3-2024, "2025-01-15", ...
    kind: ClaimKind = "reported"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    statement: str | None = None  # verbatim support for qualitative claims
    source_chunk_id: str  # provenance — set by the pipeline, never by the model


class AgentState(TypedDict, total=False):
    """Spec section 5 contract plus Phase 3 operational extensions.

    Spec-fixed keys: query, query_type, retrieved_chunks, extracted_facts,
    comparison_table, final_answer, citations, trace_id.

    Extensions:
    - agent_path: node names that executed, in order (drives QueryResponse).
    - force_agents: /agents/query bypasses the simple-path routing decision.
    - tickers: planned entities (fetch agent input, planner output).
    - unavailable_sources: human-readable reasons a source contributed nothing.
    - node_errors: per-node failure records ({node, error, recovered}) kept
      server-side; never serialized to API clients.
    - ingested_keys: "ticker:source_type" pairs already live-ingested during
      this run — loop protection against repeated ingestion.
    - limitations: degradation notes folded into the final answer.
    - trace_urls: per-agent trace links; the best available one is surfaced.
    """

    query: str
    query_type: QueryType
    retrieved_chunks: list[RetrievedChunk]
    extracted_facts: list[dict]
    comparison_table: dict | None
    final_answer: str | None
    citations: list[dict]
    trace_id: str

    agent_path: list[str]
    force_agents: bool
    tickers: list[str]
    unavailable_sources: list[str]
    node_errors: list[dict]
    ingested_keys: list[str]
    limitations: list[str]
    trace_urls: list[str | None]


def initial_state(
    query: str,
    *,
    force_agents: bool = False,
    query_type: QueryType = "multi_hop",
) -> AgentState:
    """A fully-initialized state so every node sees all keys it may read."""
    return AgentState(
        query=query,
        query_type=query_type,
        retrieved_chunks=[],
        extracted_facts=[],
        comparison_table=None,
        final_answer=None,
        citations=[],
        trace_id="",
        agent_path=["classify"],
        force_agents=force_agents,
        tickers=[],
        unavailable_sources=[],
        node_errors=[],
        ingested_keys=[],
        limitations=[],
        trace_urls=[],
    )


def unique_chunks(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """chunk-id-deduplicated view, best score first, stable otherwise."""
    best: dict[str, RetrievedChunk] = {}
    for chunk in chunks:
        current = best.get(chunk.chunk_id)
        if current is None or chunk.score > current.score:
            best[chunk.chunk_id] = chunk
    return sorted(best.values(), key=lambda c: (-c.score, c.chunk_id))


def merge_notes(
    state: AgentState,
    *,
    unavailable: list[str] | None = None,
    limitations: list[str] | None = None,
    errors: list[dict] | None = None,
) -> dict:
    """Partial-update fragment merging diagnostic lists onto existing ones."""
    updates: dict[str, Any] = {}
    if unavailable:
        merged = list(dict.fromkeys([*state.get("unavailable_sources", []), *unavailable]))
        updates["unavailable_sources"] = merged
    if limitations:
        merged = list(dict.fromkeys([*state.get("limitations", []), *limitations]))
        updates["limitations"] = merged
    if errors:
        updates["node_errors"] = [*state.get("node_errors", []), *errors]
    return updates
