"""POST /ingest — run the ingestion pipeline (spec section 12)."""

from fastapi import APIRouter, Request

from api.errors import ApiError
from api.schemas import IngestionFailureModel, IngestionResponse, IngestRequest
from observability.metrics import METRICS

router = APIRouter()


@router.post("/ingest", response_model=IngestionResponse)
def ingest(body: IngestRequest, request: Request) -> IngestionResponse:
    pipeline = request.app.state.pipeline
    if pipeline is None:
        raise ApiError(503, "ingestion_unavailable", "Ingestion pipeline is not configured.")
    if not pipeline.store.is_ready():
        raise ApiError(
            503,
            "vector_store_not_ready",
            "Vector store is not configured. Set PINECONE_API_KEY to enable ingestion.",
        )
    if not pipeline.engine.has_embedding():
        raise ApiError(
            503,
            "no_embedding_provider",
            "No embedding-capable LLM provider is available. Configure "
            "OPENAI_API_KEY (or a local Ollama embedding model) and retry.",
        )

    try:
        stats = pipeline.ingest(body.to_query_params(), source_type=body.source_type)
        METRICS.record_ingest(
            documents_count=stats.documents_ingested,
            chunks_count=stats.chunks_indexed,
            duration_ms=stats.duration_ms,
            ok=stats.ok,
        )
    except ValueError as exc:
        METRICS.record_ingest(documents_count=0, chunks_count=0, duration_ms=0.0, ok=False)
        # Unknown source_type or unavailable adapter: a client/config problem.
        raise ApiError(400, "invalid_source", str(exc)) from exc
    except Exception:
        METRICS.record_ingest(documents_count=0, chunks_count=0, duration_ms=0.0, ok=False)
        raise

    if stats.documents_fetched == 0 and any(f.stage == "fetch" for f in stats.failures):
        detail = [failure.__dict__ for failure in stats.failures]
        raise ApiError(502, "source_fetch_failed", "Upstream source fetch failed.", detail)

    return IngestionResponse(
        documents_fetched=stats.documents_fetched,
        documents_ingested=stats.documents_ingested,
        chunks_indexed=stats.chunks_indexed,
        chunks_truncated_for_metadata=stats.chunks_truncated_for_metadata,
        documents_failed=stats.documents_failed,
        failures=[IngestionFailureModel(**failure.__dict__) for failure in stats.failures],
        embedding_provider=stats.embedding_provider,
        embedding_model=stats.embedding_model,
        duration_ms=round(stats.duration_ms, 2),
        ok=stats.ok,
    )
