"""Shared Pydantic models for the ingestion/retrieval pipeline (spec section 5).

Phase 2 adds QueryResponse (the /query API contract); AgentState arrives with
the agent phases.
"""

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class RawDocument(BaseModel):
    """A document as fetched straight from a data source, before chunking."""

    source_id: str  # e.g. "SEC:AAPL:10-K:2024-11-01"
    source_type: str  # "sec_filing" | "news" | "transcript" | "apex_portfolio"
    title: str
    published_date: date | None = None
    raw_text: str
    metadata: dict[str, Any] = Field(default_factory=dict)  # ticker, filing type, url, ...


class Chunk(BaseModel):
    chunk_id: str
    source_id: str
    source_type: str
    section: str | None = None  # e.g. "Item 7 - Management's Discussion and Analysis"
    page_or_position: str = ""
    text: str
    entities: list[str] = Field(default_factory=list)  # filled by entity_extractor (later phase)
    # Extension to spec section 5, required by section 7: footnotes attach here
    # (metadata["footnotes"]) instead of becoming standalone chunks, and
    # per-document metadata (ticker, filing date, title) flows through so the
    # vector store can index filterable fields.
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def footnotes(self) -> list[str]:
        return self.metadata.get("footnotes", [])


class RetrievedChunk(Chunk):
    score: float = 0.0


class Citation(BaseModel):
    """One source backing part of an answer (spec section 5: citations are
    dicts of {source_id, title, excerpt, url}; chunk_id/score/section ride
    along for the frontend's CitationCard)."""

    source_id: str
    title: str
    excerpt: str
    url: str | None = None
    chunk_id: str | None = None
    score: float | None = None
    section: str | None = None
    page_or_position: str | None = None


class QueryResponse(BaseModel):
    """Contract for POST /query (spec section 5)."""

    answer: str
    citations: list[Citation]  # every entry maps to a real retrieved chunk
    agent_path: list[str]  # which steps ran, in order ("rewrite", "retrieve", ...)
    trace_url: str | None = None  # Langfuse link when tracing is configured
