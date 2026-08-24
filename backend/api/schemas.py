"""API request/response models (spec section 12).

Endpoint-specific DTOs live here; QueryResponse/Citation (the shared query
contract) live in models/schemas.py. All request models validate sizes and
shapes so malformed input dies at 422 with a consistent error envelope.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from models.schemas import Citation, QueryResponse

__all__ = [
    "Citation",
    "HealthResponse",
    "IngestRequest",
    "IngestionFailureModel",
    "IngestionResponse",
    "ProvidersResponse",
    "QueryFilters",
    "QueryRequest",
    "QueryResponse",
    "ReadyResponse",
    "SourcesResponse",
]


class QueryFilters(BaseModel):
    """Optional retrieval filters (spec section 8.2 subset). Unknown filter
    keys are rejected rather than silently ignored — a typo'd filter that
    silently matched everything would quietly widen a restricted query."""

    model_config = ConfigDict(extra="forbid")

    ticker: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9.\-]{0,5}$")
    source_type: str | None = Field(default=None, pattern=r"^[a-z_]{2,30}$")
    date_start: date | None = None
    date_end: date | None = None

    def to_store_filters(self) -> dict:
        filters: dict = {}
        if self.ticker:
            filters["ticker"] = self.ticker.upper()
        if self.source_type:
            filters["source_type"] = self.source_type
        if self.date_start or self.date_end:
            filters["date_range"] = [
                self.date_start.isoformat() if self.date_start else None,
                self.date_end.isoformat() if self.date_end else None,
            ]
        return filters


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    filters: QueryFilters | None = None


class IngestRequest(BaseModel):
    source_type: str = Field(default="sec_filing", pattern=r"^[a-z_]{2,30}$")
    ticker: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9.\-]{0,5}$")
    filing_type: str | None = Field(default=None, pattern=r"^[A-Za-z0-9\-]{1,12}$")
    query: str | None = Field(default=None, min_length=2, max_length=500)
    date_range: tuple[str, str] | None = None
    limit: int = Field(default=5, ge=1, le=25)

    @field_validator("date_range")
    @classmethod
    def _validate_date_range(cls, value: tuple[str, str] | None) -> tuple[str, str] | None:
        if value is None:
            return None
        start, end = value
        try:
            date.fromisoformat(start)
            date.fromisoformat(end)
        except ValueError as exc:
            raise ValueError("date_range entries must be ISO dates (YYYY-MM-DD)") from exc
        if start > end:
            raise ValueError("date_range start must be <= end")
        return value

    @model_validator(mode="after")
    def _require_ticker_or_query(self) -> "IngestRequest":
        if not self.ticker and not self.query:
            raise ValueError("ingest requires 'ticker' or 'query'")
        return self

    def to_query_params(self) -> dict:
        params: dict = {"limit": self.limit}
        if self.ticker:
            params["ticker"] = self.ticker.upper()
        if self.filing_type:
            params["filing_type"] = self.filing_type
        if self.query:
            params["query"] = self.query
        if self.date_range:
            params["date_range"] = list(self.date_range)
        return params


class IngestionFailureModel(BaseModel):
    source_id: str
    stage: str
    error: str


class IngestionResponse(BaseModel):
    documents_fetched: int
    documents_ingested: int
    chunks_indexed: int
    chunks_truncated_for_metadata: int = 0
    documents_failed: int = 0
    failures: list[IngestionFailureModel] = Field(default_factory=list)
    embedding_provider: str | None = None
    embedding_model: str | None = None
    duration_ms: float
    ok: bool


class SourcesResponse(BaseModel):
    """Spec section 12 shape: adapter name -> usable right now."""

    sec_edgar: bool
    news_api: bool = False  # Phase 3
    apex: bool = False  # optional adapter, disabled by default (spec 6.4)


class ProvidersResponse(BaseModel):
    """Spec section 12 shape plus operational extras."""

    available: list[str]
    generation_default: str | None = None
    embedding_available: bool = False
    embedding_model: str | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    env: str


class ReadyResponse(BaseModel):
    status: str  # "ready" | "degraded"
    checks: dict[str, object]
