"""Tests for structured logging, correlation IDs, and operational metrics."""

import json
import logging

import pytest
from fakes import FakeVectorStore, ScriptedProvider
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from llm_providers.engine import LLMEngine
from observability.logging import JsonLogFormatter, get_current_request_id, set_current_request_id
from observability.metrics import METRICS


@pytest.fixture(autouse=True)
def reset_metrics_state():
    METRICS.reset()
    yield
    METRICS.reset()


def test_json_log_formatter_structure():
    formatter = JsonLogFormatter(env="staging")
    record = logging.LogRecord(
        name="api.test",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Query executed successfully for %s",
        args=("AAPL",),
        exc_info=None,
    )
    record.request_id = "req-abc-123"

    formatted = formatter.format(record)
    parsed = json.loads(formatted)

    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "api.test"
    assert parsed["message"] == "Query executed successfully for AAPL"
    assert parsed["request_id"] == "req-abc-123"
    assert parsed["env"] == "staging"
    assert "timestamp" in parsed


def test_json_log_formatter_scrubs_secrets():
    formatter = JsonLogFormatter(env="prod")
    record = logging.LogRecord(
        name="llm.provider",
        level=logging.WARNING,
        pathname="test.py",
        lineno=20,
        msg="Failed with key sk-proj-1234567890abcdef and token Bearer my-secret-token",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    parsed = json.loads(formatted)

    assert "sk-proj-1234567890abcdef" not in parsed["message"]
    assert "<redacted>" in parsed["message"]


def test_request_id_context_var():
    assert get_current_request_id() is None
    set_current_request_id("ctx-req-001")
    try:
        assert get_current_request_id() == "ctx-req-001"
    finally:
        set_current_request_id(None)
    assert get_current_request_id() is None


def test_metrics_registry_recording():
    METRICS.record_http_request("GET", "/health", 200, 15.5)
    METRICS.record_http_request("POST", "/query", 200, 250.0)
    METRICS.record_http_request("POST", "/query", 429, 2.0)
    METRICS.record_query("simple", 250.0, citations_count=3, ok=True)
    METRICS.record_query("multi_hop", 500.0, citations_count=6, ok=True)
    METRICS.record_ingest(documents_count=1, chunks_count=20, duration_ms=1200.0, ok=True)
    METRICS.record_provider_call(
        "openai", "generate", ok=True, prompt_tokens=100, completion_tokens=50
    )

    snapshot = METRICS.get_snapshot()

    assert snapshot["http"]["status_codes"][200] == 2
    assert snapshot["http"]["status_codes"][429] == 1
    assert snapshot["http"]["rate_limit_rejections"] == 1
    assert snapshot["queries"]["total"] == 2
    assert snapshot["queries"]["simple"] == 1
    assert snapshot["queries"]["multi_hop"] == 1
    assert snapshot["queries"]["citations_returned"] == 9
    assert snapshot["ingestion"]["documents_ingested"] == 1
    assert snapshot["ingestion"]["chunks_indexed"] == 20
    assert snapshot["providers"]["tokens"]["openai"]["total_tokens"] == 150


def test_metrics_endpoint_response():
    settings = Settings(
        sentinel_env="dev", sec_contact_email="tester@domain.com", auth_enabled=False
    )
    app = create_app(
        settings=settings,
        engine=LLMEngine(providers=[ScriptedProvider("fake-llm")]),
        store=FakeVectorStore(),
    )
    client = TestClient(app)

    # Perform a request to generate metrics
    client.get("/health")

    res = client.get("/metrics")
    assert res.status_code == 200
    payload = res.json()

    assert "uptime_seconds" in payload
    assert "http" in payload
    assert "queries" in payload
    assert "ingestion" in payload
    assert "providers" in payload
    assert payload["http"]["routes"]["GET /health"]["requests"] >= 1
