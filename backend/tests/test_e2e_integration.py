"""End-to-end integration tests covering authenticated workflows, correlation, and provider states."""

import pytest
from fakes import (
    FakeAdapter,
    FakeVectorStore,
    ScriptedProvider,
)
from fastapi.testclient import TestClient
from pydantic import SecretStr

from api.main import create_app
from config.settings import Settings
from llm_providers.engine import LLMEngine
from models.schemas import Chunk, RawDocument
from observability.metrics import METRICS


@pytest.fixture(autouse=True)
def clean_metrics():
    METRICS.reset()
    yield
    METRICS.reset()


def test_e2e_authenticated_query_and_metrics():
    settings = Settings(
        sentinel_env="staging",
        sec_contact_email="tester@domain.com",
        auth_enabled=True,
        auth_api_key=SecretStr("staging-key-abc"),
        embedding_dimension=3,
    )
    store = FakeVectorStore()
    chunk = Chunk(
        chunk_id="chunk-001",
        source_id="SEC:AAPL:10-K:2024",
        source_type="sec_filing",
        section="Item 7",
        page_or_position="p. 1",
        text="Apple revenue for fiscal 2024 was $391 billion.",
        entities=["AAPL", "2024", "$391 billion"],
    )
    store.add([chunk], [[10.0, 1.0, 0.5]])

    app = create_app(
        settings=settings,
        engine=LLMEngine(
            providers=[
                ScriptedProvider(
                    "fake-llm",
                    generation_script=["Apple revenue for fiscal 2024 was $391 billion [1]."],
                )
            ]
        ),
        store=store,
    )
    client = TestClient(app)

    # 1. Unauthenticated request fails with 401
    res_unauth = client.post("/query", json={"question": "What was Apple revenue in 2024?"})
    assert res_unauth.status_code == 401
    assert res_unauth.json()["error"]["code"] == "unauthorized"

    # 2. Authenticated request with custom X-Request-ID succeeds
    headers = {
        "Authorization": "Bearer staging-key-abc",
        "X-Request-ID": "e2e-trace-session-001",
    }
    res_auth = client.post(
        "/query",
        json={"question": "What was Apple revenue in 2024?"},
        headers=headers,
    )
    assert res_auth.status_code == 200
    assert res_auth.headers["X-Request-ID"] == "e2e-trace-session-001"
    payload = res_auth.json()
    assert "answer" in payload
    assert len(payload["citations"]) > 0
    # 3. Check metrics endpoint captures the transaction
    res_metrics = client.get("/metrics", headers=headers)
    assert res_metrics.status_code == 200
    metrics = res_metrics.json()
    assert metrics["queries"]["total"] >= 1
    assert metrics["http"]["auth_rejections"] >= 1
    assert metrics["http"]["status_codes"]["200"] >= 1


def test_e2e_provider_degraded_state_handling():
    settings = Settings(
        sentinel_env="dev",
        sec_contact_email="tester@domain.com",
        auth_enabled=False,
    )
    # Store not ready
    unready_store = FakeVectorStore(ready=False)

    app = create_app(
        settings=settings,
        engine=LLMEngine(providers=[ScriptedProvider("fake-llm")]),
        store=unready_store,
    )
    client = TestClient(app)

    # Liveness is healthy
    res_health = client.get("/health")
    assert res_health.status_code == 200

    # Readiness is 503 degraded
    res_ready = client.get("/ready")
    assert res_ready.status_code == 503
    assert res_ready.json()["error"]["code"] == "not_ready"

    # Query fails gracefully with 503
    res_query = client.post("/query", json={"question": "What is AAPL debt?"})
    assert res_query.status_code == 503
    assert res_query.json()["error"]["code"] == "vector_store_not_ready"


def test_e2e_ingestion_and_query_flow():
    settings = Settings(
        sentinel_env="dev",
        sec_contact_email="tester@domain.com",
        auth_enabled=False,
        embedding_dimension=3,
    )
    store = FakeVectorStore()
    doc = RawDocument(
        source_id="SEC:AAPL:10-K:2024",
        source_type="sec_filing",
        title="Apple 10-K",
        raw_text="Item 7. Management Discussion and Analysis\n\nApple total net sales were $391 billion in fiscal 2024.",
        metadata={"ticker": "AAPL", "title": "Apple 10-K"},
    )
    app = create_app(
        settings=settings,
        engine=LLMEngine(
            providers=[
                ScriptedProvider(
                    "fake-llm",
                    generation_script=["Apple filings summarize robust performance [1]."],
                )
            ]
        ),
        store=store,
        adapters={"sec_filing": FakeAdapter(documents=[doc], name="sec_filing", available=True)},
    )
    client = TestClient(app)

    # Ingest document
    res_ingest = client.post(
        "/ingest",
        json={"source_type": "sec_filing", "ticker": "AAPL", "limit": 1},
    )
    assert res_ingest.status_code == 200
    assert res_ingest.json()["ok"] is True
    assert res_ingest.json()["chunks_indexed"] > 0

    # Query the ingested content
    res_query = client.post(
        "/query",
        json={"question": "Summarize Apple filings"},
    )
    assert res_query.status_code == 200
    assert len(res_query.json()["citations"]) > 0
