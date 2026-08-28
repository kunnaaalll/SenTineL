"""Sentinel FastAPI application (spec section 12, docs/API.md, docs/OPERATIONS.md).

create_app() wires the components (LLM engine, vector store, adapters,
ingestion pipeline, RAG chain, agent team, query service, tracer, metrics)
into app.state; every route reads them from there, making the entire API
injectable for offline testing.

Security & Operations:
- Single-user staging authentication (API key / Bearer token validation)
- In-memory rate limiting and request payload bounds
- Defensive security headers, CORS, and Trusted Host filtering
- Structured JSON logging and correlation ID propagation (X-Request-ID)
- Liveness (/health), readiness (/ready), and operational metrics (/metrics)
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from agents.compare_agent import CompareAgent
from agents.extract_agent import ExtractAgent
from agents.fetch_agent import FetchAgent
from agents.graph import SentinelQueryService
from agents.synthesize_agent import SynthesizeAgent
from api.errors import register_error_handlers
from api.middleware import (
    AuthenticationMiddleware,
    MetricsMiddleware,
    RateLimitMiddleware,
    RequestIdMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from api.routes_ingest import router as ingest_router
from api.routes_metrics import router as metrics_router
from api.routes_providers import router as providers_router
from api.routes_query import router as query_router
from api.routes_sources import router as sources_router
from chains.query_rewrite import QueryRewriter
from chains.rag_chain import RagChain
from config.settings import Settings, get_settings
from data_sources.base import DataSourceAdapter
from data_sources.news_api import NewsApiAdapter
from ingestion.pipeline import IngestionPipeline
from llm_providers.engine import LLMEngine
from observability.langfuse_wrapper import get_tracer
from observability.logging import configure_logging
from retrieval.pinecone_store import PineconeVectorStore

logger = logging.getLogger(__name__)

API_VERSION = "0.1.0-rc1"


def default_adapters(settings: Settings) -> dict[str, DataSourceAdapter]:
    """Registry keyed by source_type. The APEX adapter stays unregistered and
    disabled by default (spec section 6.4); the news adapter is constructed
    unconditionally and reports is_available() == False without a key."""
    from data_sources.sec_edgar import SecEdgarAdapter

    return {
        "sec_filing": SecEdgarAdapter(settings=settings),
        "news": NewsApiAdapter(settings=settings),
    }


def create_app(
    *,
    settings: Settings | None = None,
    engine: LLMEngine | None = None,
    store=None,
    adapters: dict[str, DataSourceAdapter] | None = None,
    pipeline: IngestionPipeline | None = None,
    rag_chain: RagChain | None = None,
    rewriter: QueryRewriter | None = None,
    tracer=None,
    fetch_agent: FetchAgent | None = None,
    extract_agent: ExtractAgent | None = None,
    compare_agent: CompareAgent | None = None,
    synthesize_agent: SynthesizeAgent | None = None,
    query_service: SentinelQueryService | None = None,
) -> FastAPI:
    """Build the app with any component overridden — tests pass fakes here."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings)

    resolved_tracer = tracer if tracer is not None else get_tracer(resolved_settings)
    resolved_engine = engine or LLMEngine(settings=resolved_settings, tracer=resolved_tracer)
    resolved_store = store if store is not None else PineconeVectorStore(settings=resolved_settings)
    resolved_adapters = adapters if adapters is not None else default_adapters(resolved_settings)
    resolved_rewriter = rewriter or QueryRewriter(
        settings=resolved_settings, engine=resolved_engine
    )
    resolved_pipeline = pipeline or IngestionPipeline(
        adapters=resolved_adapters,
        engine=resolved_engine,
        store=resolved_store,
        settings=resolved_settings,
        tracer=resolved_tracer,
    )
    resolved_rag = rag_chain or RagChain(
        resolved_engine,
        resolved_store,
        settings=resolved_settings,
        tracer=resolved_tracer,
        rewriter=resolved_rewriter,
    )
    # Agent team: each node independently injectable; defaults are fully
    # functional offline against whatever store/adapters were resolved above.
    resolved_fetch = fetch_agent or FetchAgent(
        engine=resolved_engine,
        store=resolved_store,
        adapters=resolved_adapters,
        pipeline=resolved_pipeline,
        settings=resolved_settings,
        tracer=resolved_tracer,
    )
    resolved_extract = extract_agent or ExtractAgent(
        engine=resolved_engine,
        settings=resolved_settings,
        tracer=resolved_tracer,
    )
    resolved_compare = compare_agent or CompareAgent()
    resolved_synthesize = synthesize_agent or SynthesizeAgent(
        engine=resolved_engine,
        settings=resolved_settings,
        tracer=resolved_tracer,
    )
    resolved_service = query_service or SentinelQueryService(
        rag_chain=resolved_rag,
        fetch_agent=resolved_fetch,
        extract_agent=resolved_extract,
        compare_agent=resolved_compare,
        synthesize_agent=resolved_synthesize,
        settings=resolved_settings,
        tracer=resolved_tracer,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        try:  # flush pending traces on shutdown
            resolved_tracer.flush()
        except Exception:  # noqa: BLE001
            logger.warning("Tracer flush failed on shutdown", exc_info=True)

    application = FastAPI(
        title="Sentinel API",
        version=API_VERSION,
        description=(
            "Agentic financial research copilot. PRIVATE/LOCAL-ONLY: secured with configurable "
            "single-user API key / Bearer authentication, rate limiting, and "
            "structured observability."
        ),
        lifespan=lifespan,
    )

    application.state.settings = resolved_settings
    application.state.engine = resolved_engine
    application.state.store = resolved_store
    application.state.adapters = resolved_adapters
    application.state.pipeline = resolved_pipeline
    application.state.rag_chain = resolved_rag
    application.state.tracer = resolved_tracer
    application.state.agents = {
        "fetch": resolved_fetch,
        "extract": resolved_extract,
        "compare": resolved_compare,
        "synthesize": resolved_synthesize,
    }
    application.state.query_service = resolved_service

    # Middleware Registration
    # Starlette executes middlewares in reverse order of addition (LIFO).
    # Desired request flow:
    # Metrics -> RequestId -> SecurityHeaders -> CORS -> TrustedHost ->
    # SizeLimit -> RateLimit -> Auth -> App
    application.add_middleware(AuthenticationMiddleware, settings=resolved_settings)
    application.add_middleware(RateLimitMiddleware, settings=resolved_settings)
    application.add_middleware(
        RequestSizeLimitMiddleware, max_bytes=resolved_settings.max_request_body_bytes
    )
    if resolved_settings.parsed_allowed_hosts != ["*"]:
        application.add_middleware(
            TrustedHostMiddleware, allowed_hosts=resolved_settings.parsed_allowed_hosts
        )
    application.add_middleware(SecurityHeadersMiddleware, settings=resolved_settings)
    application.add_middleware(RequestIdMiddleware)
    application.add_middleware(MetricsMiddleware)
    cors_origins = resolved_settings.parsed_cors_origins
    cors_kwargs: dict = {
        "allow_credentials": True,
        "allow_methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["*"],
        "expose_headers": ["X-Request-ID", "Retry-After"],
    }
    if "*" in cors_origins or not resolved_settings.cors_allowed_origins:
        cors_kwargs["allow_origin_regex"] = r"https?://.*"
    else:
        cors_kwargs["allow_origins"] = cors_origins
    application.add_middleware(CORSMiddleware, **cors_kwargs)

    # Routes & Error Handling
    application.include_router(query_router)
    application.include_router(ingest_router)
    application.include_router(sources_router)
    application.include_router(providers_router)
    application.include_router(metrics_router)
    register_error_handlers(application)

    return application


app = create_app()
