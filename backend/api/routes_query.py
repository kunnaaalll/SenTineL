"""POST /query + POST /agents/query (spec section 12).

/query classifies automatically: simple questions ride the existing RagChain,
multi-hop questions run the LangGraph agent team. /agents/query forces the
agent path regardless of classification (demo/testing hook). Both routes
return the same QueryResponse contract; agent_path always starts with
"classify" and reflects what actually ran.
"""

from fastapi import APIRouter, Request

from api.errors import ApiError
from api.schemas import QueryRequest
from llm_providers.base import ProviderUnavailableError
from models.schemas import Citation, QueryResponse

router = APIRouter()


@router.post("/query", response_model=QueryResponse)
def query(body: QueryRequest, request: Request) -> QueryResponse:
    return _run_query(request, body, force_agents=False)


@router.post("/agents/query", response_model=QueryResponse)
def agents_query(body: QueryRequest, request: Request) -> QueryResponse:
    return _run_query(request, body, force_agents=True)


def _run_query(request: Request, body: QueryRequest, *, force_agents: bool) -> QueryResponse:
    service = request.app.state.query_service
    if service is None:
        raise ApiError(503, "query_unavailable", "Query service is not configured.")
    if not request.app.state.engine.has_embedding():
        raise ApiError(
            503,
            "no_embedding_provider",
            "No embedding-capable LLM provider is available. Configure "
            "OPENAI_API_KEY (or a local Ollama embedding model) and retry.",
        )
    if not request.app.state.store.is_ready():
        raise ApiError(
            503,
            "vector_store_not_ready",
            "Vector store is not configured. Set PINECONE_API_KEY to enable queries.",
        )

    try:
        result = service.answer(
            body.question,
            force_agents=force_agents,
            top_k=body.top_k,
            filters=body.filters.to_store_filters() if body.filters else None,
        )
    except ProviderUnavailableError as exc:
        # Only the simple path can surface this; the agent path degrades to a
        # grounded digest instead of raising.
        raise ApiError(503, "no_llm_provider", str(exc)) from exc

    return QueryResponse(
        answer=result.answer,
        citations=[Citation(**citation) for citation in result.citations],
        agent_path=result.agent_path,
        trace_url=result.trace_url,
    )
