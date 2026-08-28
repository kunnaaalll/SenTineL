"""Tests for API protection, authentication, rate limiting, and security headers."""

import pytest
from fakes import (
    FakeVectorStore,
    ScriptedProvider,
)
from fastapi.testclient import TestClient
from pydantic import SecretStr

from api.main import create_app
from config.settings import Settings
from llm_providers.engine import LLMEngine


@pytest.fixture
def base_settings():
    return Settings(
        sentinel_env="dev",
        sec_contact_email="tester@domain.com",
        auth_enabled=False,
        rate_limit_enabled=True,
        rate_limit_requests_per_minute=60,
        rate_limit_burst_limit=5,
        max_request_body_bytes=1000,
        cors_allowed_origins="http://127.0.0.1:3000,http://localhost:3000",
        allowed_hosts="*",
        security_headers_enabled=True,
    )


@pytest.fixture
def auth_settings():
    return Settings(
        sentinel_env="staging",
        sec_contact_email="tester@domain.com",
        auth_enabled=True,
        auth_api_key=SecretStr("staging-secret-key-123"),
        rate_limit_enabled=True,
        rate_limit_requests_per_minute=60,
        rate_limit_burst_limit=5,
        max_request_body_bytes=1000,
        cors_allowed_origins="http://127.0.0.1:3000,http://localhost:3000",
        allowed_hosts="*",
        security_headers_enabled=True,
    )


def test_public_endpoints_exempt_from_auth(auth_settings):
    app = create_app(
        settings=auth_settings,
        engine=LLMEngine(providers=[ScriptedProvider("fake-llm")]),
        store=FakeVectorStore(),
    )
    client = TestClient(app)

    # /health is public
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # /ready is public
    res = client.get("/ready")
    assert res.status_code == 200
    assert res.json()["status"] == "ready"

    # /openapi.json is public
    res = client.get("/openapi.json")
    assert res.status_code == 200


def test_unauthenticated_request_rejected(auth_settings):
    app = create_app(
        settings=auth_settings,
        engine=LLMEngine(providers=[ScriptedProvider("fake-llm")]),
        store=FakeVectorStore(),
    )
    client = TestClient(app)

    res = client.get("/sources")
    assert res.status_code == 401
    payload = res.json()
    assert "error" in payload
    assert payload["error"]["code"] == "unauthorized"
    assert "Authentication required" in payload["error"]["message"]


def test_authenticated_with_bearer_token(auth_settings):
    app = create_app(
        settings=auth_settings,
        engine=LLMEngine(providers=[ScriptedProvider("fake-llm")]),
        store=FakeVectorStore(),
    )
    client = TestClient(app)

    res = client.get("/sources", headers={"Authorization": "Bearer staging-secret-key-123"})
    assert res.status_code == 200
    assert "sec_edgar" in res.json()


def test_authenticated_with_x_api_key(auth_settings):
    app = create_app(
        settings=auth_settings,
        engine=LLMEngine(providers=[ScriptedProvider("fake-llm")]),
        store=FakeVectorStore(),
    )
    client = TestClient(app)

    res = client.get("/sources", headers={"X-API-Key": "staging-secret-key-123"})
    assert res.status_code == 200
    assert "sec_edgar" in res.json()


def test_invalid_credentials_rejected(auth_settings):
    app = create_app(
        settings=auth_settings,
        engine=LLMEngine(providers=[ScriptedProvider("fake-llm")]),
        store=FakeVectorStore(),
    )
    client = TestClient(app)

    res = client.get("/sources", headers={"Authorization": "Bearer wrong-token"})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"
    assert res.json()["error"]["message"] == "Invalid authentication credentials."


def test_rate_limiting_enforcement(base_settings):
    base_settings.rate_limit_burst_limit = 3
    base_settings.rate_limit_requests_per_minute = 60
    app = create_app(
        settings=base_settings,
        engine=LLMEngine(providers=[ScriptedProvider("fake-llm")]),
        store=FakeVectorStore(),
    )
    client = TestClient(app)

    # 3 requests allowed under burst
    for _ in range(3):
        res = client.get("/sources")
        assert res.status_code == 200

    # 4th request exceeds burst limit -> 429
    res = client.get("/sources")
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in res.headers


def test_request_size_limit(base_settings):
    base_settings.max_request_body_bytes = 50
    app = create_app(
        settings=base_settings,
        engine=LLMEngine(providers=[ScriptedProvider("fake-llm")]),
        store=FakeVectorStore(),
    )
    client = TestClient(app)

    # Payload larger than 50 bytes
    large_payload = {"question": "A" * 100}
    res = client.post("/query", json=large_payload)
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "payload_too_large"


def test_security_headers_present(base_settings):
    app = create_app(
        settings=base_settings,
        engine=LLMEngine(providers=[ScriptedProvider("fake-llm")]),
        store=FakeVectorStore(),
    )
    client = TestClient(app)

    res = client.get("/health")
    assert res.status_code == 200
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"
    assert res.headers["X-XSS-Protection"] == "1; mode=block"
    assert res.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "default-src 'self'" in res.headers["Content-Security-Policy"]


def test_cors_headers(base_settings):
    app = create_app(
        settings=base_settings,
        engine=LLMEngine(providers=[ScriptedProvider("fake-llm")]),
        store=FakeVectorStore(),
    )
    client = TestClient(app)

    # CORS Preflight
    res = client.options(
        "/query",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-request-id",
        },
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"

    # Actual request
    res = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_correlation_id_propagation(base_settings):
    app = create_app(
        settings=base_settings,
        engine=LLMEngine(providers=[ScriptedProvider("fake-llm")]),
        store=FakeVectorStore(),
    )
    client = TestClient(app)

    # Explicit Request ID
    res = client.get("/health", headers={"X-Request-ID": "test-custom-id-999"})
    assert res.status_code == 200
    assert res.headers["X-Request-ID"] == "test-custom-id-999"

    # Generated Request ID
    res_auto = client.get("/health")
    assert res_auto.status_code == 200
    assert "X-Request-ID" in res_auto.headers
    assert len(res_auto.headers["X-Request-ID"]) >= 16


def test_uniform_error_envelope(base_settings):
    app = create_app(
        settings=base_settings,
        engine=LLMEngine(providers=[ScriptedProvider("fake-llm")]),
        store=FakeVectorStore(),
    )
    client = TestClient(app)

    # 404 Not Found
    res = client.get("/non-existent-endpoint")
    assert res.status_code == 404
    payload = res.json()
    assert "error" in payload
    assert payload["error"]["code"] == "not_found"
    assert isinstance(payload["error"]["message"], str)

    # 405 Method Not Allowed
    res = client.get("/ingest")
    assert res.status_code == 405
    payload = res.json()
    assert "error" in payload
    assert payload["error"]["code"] == "method_not_allowed"

    # 422 Validation Error
    res = client.post("/query", json={})
    assert res.status_code == 422
    payload = res.json()
    assert "error" in payload
    assert payload["error"]["code"] == "validation_error"
    assert "details" in payload["error"]
