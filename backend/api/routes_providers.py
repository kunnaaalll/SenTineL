"""GET /providers + GET /health + GET /ready (spec section 12)."""

from fastapi import APIRouter, Request

from api.errors import ApiError
from api.schemas import HealthResponse, ProvidersResponse, ReadyResponse

router = APIRouter()


@router.get("/providers", response_model=ProvidersResponse)
def providers(request: Request) -> ProvidersResponse:
    engine = request.app.state.engine
    settings = request.app.state.settings
    available = engine.available_providers(refresh=True)
    # Surface the actual generation model in use (helps diagnose Groq/xAI issues)
    gen_model = None
    for p in engine.providers:
        if p.name in available and hasattr(p, "generation_model"):
            gen_model = p.generation_model
            break
    return ProvidersResponse(
        available=available,
        generation_default=available[0] if available else None,
        embedding_available=engine.has_embedding(),
        embedding_model=settings.openai_embedding_model if "openai" in available else None,
        generation_model=gen_model,
    )


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    return HealthResponse(
        version=request.app.version,
        env=settings.sentinel_env,
        commit_sha=settings.commit_sha,
    )


@router.get("/ready", response_model=ReadyResponse)
def ready(request: Request) -> ReadyResponse:
    """Readiness = the service can actually serve /query and /ingest. Without
    an embedding provider or a configured vector store the API still runs
    (documented degraded mode) but reports not-ready with a 503."""
    engine = request.app.state.engine
    store = request.app.state.store
    tracer = request.app.state.tracer

    checks: dict[str, object] = {
        "providers": engine.available_providers(refresh=True),
        "embedding_available": engine.has_embedding(),
        "vector_store_ready": bool(store.is_ready()),
        # LangfuseTracer instances carry an SDK client; NullTracer doesn't.
        "tracing_enabled": hasattr(tracer, "_client"),
    }
    is_ready = checks["embedding_available"] and checks["vector_store_ready"]
    if not is_ready:
        raise ApiError(503, "not_ready", "Dependencies not configured yet.", checks)
    return ReadyResponse(status="ready", checks=checks)
