"""Phase 2 API contract tests (offline).

Every endpoint is exercised through FastAPI's TestClient with fakes injected
via create_app() — no OpenAI, no Pinecone, no network. Covers request/response
shapes, validation bounds, the consistent error envelope, missing-provider and
missing-key degradation, and the absence of internal-detail leaks.
"""

from typing import cast

import pytest
from fakes import (
    FakeAdapter,
    FakeVectorStore,
    RecordingTracer,
    ScriptedProvider,
)
from fastapi.testclient import TestClient

from api.main import API_VERSION, create_app
from chains.rag_chain import RagAnswer, RagChain
from llm_providers.engine import LLMEngine
from models.schemas import RawDocument, RetrievedChunk

# --------------------------------------------------------------------------
# App construction helpers
# --------------------------------------------------------------------------


def build_client(
    *,
    documents=None,
    store=None,
    providers=None,
    settings_overrides=None,
    rag_answer=None,
    clean_settings=None,
):
    """App with fakes wired in; returns (client, components)."""
    settings = clean_settings(embedding_dimension=3, **(settings_overrides or {}))
    engine = LLMEngine(providers=providers or [ScriptedProvider("fake-llm")])
    store = store if store is not None else FakeVectorStore()
    adapter = FakeAdapter(documents or [], name="sec_edgar")
    from ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline(
        adapters={"sec_filing": adapter}, engine=engine, store=store, settings=settings
    )

    class StubRag:
        def __init__(self):
            self.engine = engine
            self.store = store

        def run(self, question, *, top_k=None, filters=None):
            if callable(rag_answer):
                return rag_answer(question)
            return RagAnswer(
                answer="Stub answer [1].",
                citations=[
                    {
                        "source_id": "SEC:AAPL:10-K:2024-11-01",
                        "title": "Apple 10-K",
                        "excerpt": "Revenue was $391,035 million.",
                        "url": "https://www.sec.gov/x.htm",
                        "chunk_id": "abc",
                        "score": 0.9,
                    }
                ],
                agent_path=["rewrite", "embed", "retrieve", "generate"],
                trace_url=None,
            )

    app = create_app(
        settings=settings,
        engine=engine,
        store=store,
        adapters={"sec_filing": adapter},
        pipeline=pipeline,
        # StubRag duck-types RagChain (routes only call .run()).
        rag_chain=cast(RagChain, StubRag()),
    )
    return TestClient(app, raise_server_exceptions=False), {
        "engine": engine,
        "store": store,
        "pipeline": pipeline,
        "adapter": adapter,
    }


@pytest.fixture()
def client(clean_settings):
    built = build_client(clean_settings=clean_settings)
    return built[0]


# --------------------------------------------------------------------------
# POST /query
# --------------------------------------------------------------------------


class TestQueryEndpoint:
    def test_happy_path_contract(self, clean_settings):
        client, _ = build_client(clean_settings=clean_settings)
        response = client.post("/query", json={"question": "What was Apple's revenue?"})
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"answer", "citations", "agent_path", "trace_url"}
        assert body["answer"] == "Stub answer [1]."
        citation = body["citations"][0]
        assert set(citation) >= {"source_id", "title", "excerpt", "url"}
        # Phase 3: routing decision is recorded as the first step.
        assert body["agent_path"] == ["classify", "rewrite", "embed", "retrieve", "generate"]
        assert body["trace_url"] is None

    def test_filters_and_top_k_accepted(self, clean_settings):
        client, _ = build_client(clean_settings=clean_settings)
        response = client.post(
            "/query",
            json={
                "question": "revenue?",
                "top_k": 3,
                "filters": {"ticker": "aapl", "date_start": "2024-01-01"},
            },
        )
        assert response.status_code == 200

    def test_empty_question_rejected_422(self, clean_settings):
        client, _ = build_client(clean_settings=clean_settings)
        response = client.post("/query", json={"question": ""})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    def test_oversized_question_rejected_422(self, clean_settings):
        client, _ = build_client(clean_settings=clean_settings)
        response = client.post("/query", json={"question": "x" * 5000})
        assert response.status_code == 422

    def test_top_k_bounds_enforced(self, clean_settings):
        client, _ = build_client(clean_settings=clean_settings)
        for bad_top_k in (0, 21):
            response = client.post("/query", json={"question": "q?", "top_k": bad_top_k})
            assert response.status_code == 422

    def test_unknown_filter_field_rejected(self, clean_settings):
        client, _ = build_client(clean_settings=clean_settings)
        response = client.post(
            "/query", json={"question": "q?", "filters": {"issuing_company": "AAPL"}}
        )
        assert response.status_code == 422

    def test_no_embedding_provider_maps_to_503_envelope(self, clean_settings):
        dead_provider = ScriptedProvider("dead", available=False)
        client, _ = build_client(providers=[dead_provider], clean_settings=clean_settings)
        response = client.post("/query", json={"question": "q?"})
        assert response.status_code == 503
        error = response.json()["error"]
        assert error["code"] == "no_embedding_provider"
        assert "OPENAI_API_KEY" in error["message"]

    def test_vector_store_not_ready_maps_to_503(self, clean_settings):
        store = FakeVectorStore(ready=False)
        client, _ = build_client(store=store, clean_settings=clean_settings)
        response = client.post("/query", json={"question": "q?"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "vector_store_not_ready"


# --------------------------------------------------------------------------
# POST /ingest
# --------------------------------------------------------------------------


def make_doc(source_id="SEC:AAPL:10-K:2024-11-01") -> RawDocument:
    return RawDocument(
        source_id=source_id,
        source_type="sec_filing",
        title="Apple 10-K",
        raw_text="Item 7. MD&A\n\nRevenue was $391,035 million, up 5%.",
        metadata={"ticker": "AAPL", "title": "Apple 10-K", "published_date": "2024-11-01"},
    )


class TestIngestEndpoint:
    def test_happy_path_contract(self, clean_settings):
        client, parts = build_client(documents=[make_doc()], clean_settings=clean_settings)
        response = client.post(
            "/ingest",
            json={"source_type": "sec_filing", "ticker": "AAPL", "filing_type": "10-K", "limit": 3},
        )
        assert response.status_code == 200
        body = response.json()
        for key in (
            "documents_fetched",
            "documents_ingested",
            "chunks_indexed",
            "chunks_truncated_for_metadata",
            "documents_failed",
            "failures",
            "embedding_provider",
            "embedding_model",
            "duration_ms",
            "ok",
        ):
            assert key in body
        assert body["documents_fetched"] == 1
        assert body["documents_ingested"] == 1
        assert body["ok"] is True
        assert body["embedding_provider"] == "fake-llm"
        # params actually reached the adapter
        assert parts["adapter"].fetch_calls[0]["ticker"] == "AAPL"
        assert parts["adapter"].fetch_calls[0]["filing_type"] == "10-K"

    def test_ticker_or_query_required_422(self, clean_settings):
        client, _ = build_client(clean_settings=clean_settings)
        response = client.post("/ingest", json={"source_type": "sec_filing"})
        assert response.status_code == 422

    def test_bad_date_range_shape_422(self, clean_settings):
        client, _ = build_client(clean_settings=clean_settings)
        response = client.post(
            "/ingest",
            json={"ticker": "AAPL", "date_range": ["2024-01-01"]},
        )
        assert response.status_code == 422

    def test_non_iso_date_rejected_422(self, clean_settings):
        client, _ = build_client(clean_settings=clean_settings)
        response = client.post(
            "/ingest",
            json={"ticker": "AAPL", "date_range": ["not-a-date", "2024-12-31"]},
        )
        assert response.status_code == 422

    def test_limit_bounds_enforced_422(self, clean_settings):
        client, _ = build_client(clean_settings=clean_settings)
        response = client.post("/ingest", json={"ticker": "AAPL", "limit": 100})
        assert response.status_code == 422

    def test_no_pinecone_key_maps_to_503(self, clean_settings):
        store = FakeVectorStore(ready=False)
        client, _ = build_client(store=store, clean_settings=clean_settings)
        response = client.post("/ingest", json={"ticker": "AAPL"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "vector_store_not_ready"

    def test_fetch_failure_maps_to_502_with_detail(self, clean_settings):
        class ExplodingAdapter(FakeAdapter):
            def fetch(self, query_params):
                raise ConnectionError("upstream down")

        settings = clean_settings(embedding_dimension=3)
        engine = LLMEngine(providers=[ScriptedProvider("p")])
        store = FakeVectorStore()
        adapter = ExplodingAdapter([])
        from ingestion.pipeline import IngestionPipeline

        pipeline = IngestionPipeline(
            adapters={"sec_filing": adapter}, engine=engine, store=store, settings=settings
        )

        class StubRag:
            def __init__(self):
                self.engine = engine
                self.store = store

            def run(self, *a, **k):  # pragma: no cover - unused here
                raise AssertionError

        app = create_app(
            settings=settings,
            engine=engine,
            store=store,
            adapters={"sec_filing": adapter},
            pipeline=pipeline,
            rag_chain=cast(RagChain, StubRag()),  # duck-typed; .run() is all routes call
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/ingest", json={"ticker": "AAPL"})
        assert response.status_code == 502
        error = response.json()["error"]
        assert error["code"] == "source_fetch_failed"
        assert error["details"][0]["stage"] == "fetch"

    def test_partial_failure_still_200_with_failures_listed(self, clean_settings):
        good = make_doc("SEC:AAPL:10-K:2024-11-01")
        bad = make_doc("SEC:BAD:10-K:2024-01-01")

        class FlakyStore(FakeVectorStore):
            def add(self, chunks, vectors):
                if any("BAD" in c.source_id for c in chunks):
                    raise RuntimeError("store exploded for BAD")
                super().add(chunks, vectors)

        client, _ = build_client(
            documents=[good, bad], store=FlakyStore(), clean_settings=clean_settings
        )
        response = client.post("/ingest", json={"query": "annual reports"})
        assert response.status_code == 200  # well-formed request, partial outcome
        body = response.json()
        assert body["documents_fetched"] == 2
        assert body["documents_ingested"] == 1
        assert body["documents_failed"] == 1
        assert body["ok"] is False
        assert body["failures"][0]["source_id"] == "SEC:BAD:10-K:2024-01-01"


# --------------------------------------------------------------------------
# GET /sources /providers /health /ready
# --------------------------------------------------------------------------


class TestInfoEndpoints:
    def test_sources_shape_sec_available_apex_disabled(self, clean_settings):
        client, _ = build_client(documents=[], clean_settings=clean_settings)
        response = client.get("/sources")
        assert response.status_code == 200
        body = response.json()
        assert body == {"sec_edgar": True, "news_api": False, "apex": False}

    def test_providers_shape_with_fake_available(self, clean_settings):
        client, _ = build_client(clean_settings=clean_settings)
        response = client.get("/providers")
        assert response.status_code == 200
        body = response.json()
        assert body["available"] == ["fake-llm"]
        assert body["generation_default"] == "fake-llm"
        assert body["embedding_available"] is True

    def test_providers_empty_when_none_available(self, clean_settings):
        dead = ScriptedProvider("dead", available=False)
        client, _ = build_client(providers=[dead], clean_settings=clean_settings)
        body = client.get("/providers").json()
        assert body["available"] == []
        assert body["generation_default"] is None
        assert body["embedding_available"] is False

    def test_health_reports_version_and_env(self, clean_settings):
        client, _ = build_client(clean_settings=clean_settings)
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["version"] == API_VERSION
        assert body["env"] == "dev"
        assert body["commit_sha"] == "dev"

    def test_ready_ok_when_dependencies_configured(self, clean_settings):
        client, _ = build_client(clean_settings=clean_settings)
        response = client.get("/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["checks"]["vector_store_ready"] is True
        assert "fake-llm" in body["checks"]["providers"]

    def test_ready_degraded_without_configuration(self, clean_settings):
        dead = ScriptedProvider("dead", available=False)
        store = FakeVectorStore(ready=False)
        client, _ = build_client(providers=[dead], store=store, clean_settings=clean_settings)
        response = client.get("/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["error"]["code"] == "not_ready"
        assert body["error"]["details"]["vector_store_ready"] is False

    def test_unknown_route_uses_error_envelope(self, client):
        response = client.get("/nonexistent")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


# --------------------------------------------------------------------------
# Error hygiene
# --------------------------------------------------------------------------


class TestErrorHygiene:
    def test_internal_errors_return_generic_envelope_without_traceback(self, clean_settings):
        def exploding_run(question, **kwargs):
            raise RuntimeError("SECRET-INTERNAL-PAYLOAD leaked")

        client, _ = build_client(rag_answer=exploding_run, clean_settings=clean_settings)
        response = client.post("/query", json={"question": "trigger"})
        assert response.status_code == 500
        body_text = response.text
        assert "SECRET-INTERNAL-PAYLOAD" not in body_text
        assert "RuntimeError" not in body_text
        assert body_text.count('"code"') == 1

    def test_validation_details_contain_only_loc_msg_type(self, clean_settings):
        client, _ = build_client(clean_settings=clean_settings)
        response = client.post("/query", json={"question": 12345, "top_k": "many"})
        details = response.json()["error"]["details"]
        for item in details:
            assert set(item) == {"loc", "msg", "type"}

    def test_app_documented_as_private_local_only(self, clean_settings):
        client, _ = build_client(clean_settings=clean_settings)
        assert "LOCAL-ONLY" in client.app.description


# --------------------------------------------------------------------------
# POST /agents/query + auto-routing (Phase 3)
# --------------------------------------------------------------------------

EXTRACT_AAPL = (
    '{"facts": [{"entity": "AAPL", "metric": "revenue", '
    '"value": "$391,035 million", "period": "FY2024", '
    '"kind": "reported", "confidence": 0.9}]}'
)
EXTRACT_MSFT = (
    '{"facts": [{"entity": "MSFT", "metric": "revenue", '
    '"value": "$245,122 million", "period": "FY2024", '
    '"kind": "reported", "confidence": 0.9}]}'
)
SYNTH_ANSWER = "Apple out-earned Microsoft in FY2024 [1][2]."


def seed_chunk(cid: str, ticker: str, text: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid,
        source_id=f"SEC:{ticker}:10-K:2024-11-01",
        source_type="sec_filing",
        section="Item 7 - MD&A",
        page_or_position="24",
        text=text,
        entities=[ticker],
        metadata={
            "ticker": ticker,
            "title": f"{ticker} 10-K",
            "url": f"https://www.sec.example/{ticker}",
            "published_date": "2024-11-01",
        },
        score=0.9,
    )


def build_agents_client(
    clean_settings,
    *,
    generation_script=None,
    store=None,
    providers=None,
    tracer=None,
):
    """App with the default agent team wired over scripted providers."""
    settings = clean_settings(embedding_dimension=3)
    engine = LLMEngine(
        providers=providers
        or [
            ScriptedProvider(
                "fake-llm",
                generation_script=generation_script or [EXTRACT_AAPL, EXTRACT_MSFT, SYNTH_ANSWER],
            )
        ]
    )
    if store is None:
        store = FakeVectorStore()
        store.add(
            [
                seed_chunk("a-aapl", "AAPL", "Total net sales $391,035 million FY2024"),
                seed_chunk("b-msft", "MSFT", "Revenue $245,122 million FY2024"),
            ],
            [[10.0, 1.0, 0.5], [11.0, 1.0, 0.5]],
        )
    adapter = FakeAdapter([], name="sec_edgar")
    from ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline(
        adapters={"sec_filing": adapter}, engine=engine, store=store, settings=settings
    )

    class StubRag:
        def run(self, question, *, top_k=None, filters=None):  # pragma: no cover
            return RagAnswer(
                answer=f"Stub answer for {question} [1].",
                citations=[{"source_id": "s", "title": "t", "excerpt": "e"}],
                agent_path=["rewrite", "embed", "retrieve", "generate"],
            )

    app = create_app(
        settings=settings,
        engine=engine,
        store=store,
        adapters={"sec_filing": adapter},
        pipeline=pipeline,
        rag_chain=cast(RagChain, StubRag()),
        tracer=tracer,
    )
    return TestClient(app, raise_server_exceptions=False), {"engine": engine, "store": store}


class TestAgentsQueryEndpoint:
    def test_forced_multi_agent_contract(self, clean_settings):
        client, _ = build_agents_client(clean_settings)
        response = client.post("/agents/query", json={"question": "Compare AAPL and MSFT revenue"})
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"answer", "citations", "agent_path", "trace_url"}
        assert body["agent_path"] == ["classify", "fetch", "extract", "compare", "synthesize"]
        assert "out-earned" in body["answer"]
        assert {c["chunk_id"] for c in body["citations"]} == {"a-aapl", "b-msft"}

    def test_query_auto_routes_multi_hop_to_agent_team(self, clean_settings):
        client, _ = build_agents_client(clean_settings)
        body = client.post("/query", json={"question": "Compare AAPL and MSFT revenue"}).json()
        assert body["agent_path"][0] == "classify"
        assert "fetch" in body["agent_path"] and "compare" in body["agent_path"]
        assert "rewrite" not in body["agent_path"]

    def test_degraded_empty_index_refuses_without_internal_leaks(self, clean_settings):
        client, _ = build_agents_client(clean_settings, store=FakeVectorStore())
        response = client.post("/agents/query", json={"question": "Compare AAPL and MSFT"})
        assert response.status_code == 200
        body = response.json()
        assert body["answer"].startswith("I couldn't gather enough indexed evidence")
        # node_errors/unavailable/limitations stay server-side; contract shape holds.
        assert set(body) == {"answer", "citations", "agent_path", "trace_url"}
        assert body["citations"] == []

    def test_no_embedding_provider_maps_503_on_both_routes(self, clean_settings):
        dead = ScriptedProvider("dead", available=False)
        client, _ = build_agents_client(clean_settings, providers=[dead])
        for route in ("/query", "/agents/query"):
            response = client.post(route, json={"question": "Compare AAPL and MSFT"})
            assert response.status_code == 503, route
            assert response.json()["error"]["code"] == "no_embedding_provider"

    def test_trace_url_surfaced_from_recording_tracer(self, clean_settings):
        tracer = RecordingTracer(url="mem://trace/api42")
        client, _ = build_agents_client(clean_settings, tracer=tracer)
        body = client.post(
            "/agents/query", json={"question": "Compare AAPL and MSFT revenue"}
        ).json()
        assert body["trace_url"] == "mem://trace/api42"

    def test_validation_still_applies_to_agents_route(self, clean_settings):
        client, _ = build_agents_client(clean_settings)
        response = client.post("/agents/query", json={"question": ""})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
