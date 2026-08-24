"""Phase 2 provider-layer tests (offline).

Covers: availability gating, fallback order, retry/backoff semantics (no
real sleeps), auth vs invalid-request routing, structured results, embedding
behavior, cost estimation, availability caching, and import safety when the
openai package or API key is missing. The OpenAI SDK surface is exercised
through a fake module injected into sys.modules — no network, ever.
"""

import types
from typing import Any

import pytest
import requests
from fakes import (
    ScriptedProvider,
    UnavailableEmbeddingProvider,
    auth_error,
    invalid_request,
    rate_limited,
    transient,
)

import llm_providers.openai_provider as openai_provider_module
from llm_providers.base import (
    AuthenticationError,
    EmbeddingResult,
    GenerationResult,
    InvalidRequestError,
    ProviderUnavailableError,
    RateLimitError,
    TokenUsage,
    TransientProviderError,
    estimate_cost_usd,
    price_for_model,
)
from llm_providers.engine import LLMEngine


def make_engine(providers, settings=None, **kwargs) -> LLMEngine:
    return LLMEngine(providers=providers, settings=settings, **kwargs)


def engine_settings(clean_settings, **overrides):
    defaults = dict(
        llm_max_retries=2,
        llm_backoff_base_seconds=0.1,
        availability_ttl_seconds=0.0,  # no caching unless a test opts in
        openai_api_key=None,
        pinecone_api_key=None,
    )
    defaults.update(overrides)
    return clean_settings(**defaults)


class NoDelaySleeper:
    """Records backoff delays instead of sleeping."""

    def __init__(self):
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


# --------------------------------------------------------------------------
# Import safety / default construction
# --------------------------------------------------------------------------


def test_engine_importable_without_keys_or_packages(clean_settings):
    settings = engine_settings(clean_settings)
    from llm_providers.engine import default_providers

    providers = default_providers(settings)
    assert [p.name for p in providers] == ["openai", "ollama"]  # configured order
    # OpenAI availability is a STATIC check (package + key) — safe to call:
    # package is installed here but the key env var is scrubbed by conftest.
    assert providers[0].is_available() is False
    # Never probe Ollama here (it would touch loopback): check its config shape.
    assert providers[1].supports_embeddings is False  # chat-only by default


def test_openai_provider_unavailable_without_key(clean_settings):
    from llm_providers.openai_provider import OpenAIProvider

    provider = OpenAIProvider(api_key=None, settings=engine_settings(clean_settings))
    assert provider.is_available() is False
    with pytest.raises(ProviderUnavailableError):
        _ = provider.client


def test_openai_provider_unavailable_when_package_missing(clean_settings, monkeypatch):
    monkeypatch.setattr(openai_provider_module, "_openai", None)
    from llm_providers.openai_provider import OpenAIProvider

    provider = OpenAIProvider(api_key="k", settings=engine_settings(clean_settings))
    assert provider.is_available() is False
    with pytest.raises(ProviderUnavailableError):
        _ = provider.client


def test_ollama_provider_defaults_local_and_optional(clean_settings):
    from llm_providers.ollama_provider import OllamaProvider

    provider = OllamaProvider(settings=engine_settings(clean_settings))
    assert provider.base_url.startswith("http://127.0.0.1")
    assert provider.supports_embeddings is False  # chat-only by default (spec 9)


# --------------------------------------------------------------------------
# Availability + fallback behavior
# --------------------------------------------------------------------------


def test_fallback_skips_unavailable_providers():
    down = ScriptedProvider("down", available=False)
    up = ScriptedProvider("up")
    engine = make_engine([down, up])
    assert engine.available_providers() == ["up"]
    result = engine.generate("hello")
    assert result.provider == "up"
    assert down.generate_calls == 0


def test_fallback_on_transient_exhaustion():
    failing = ScriptedProvider("failing", generation_script=[transient("boom")])
    working = ScriptedProvider("working")
    sleeper = NoDelaySleeper()
    engine = make_engine([failing, working], sleeper=sleeper, jitter=lambda _attempt: 0.0)
    result = engine.generate("q")
    assert result.provider == "working"
    # 3 attempts against the failing provider before falling through
    assert failing.generate_calls == 3
    assert len(sleeper.delays) == 2  # retries between attempts
    assert sleeper.delays[1] > sleeper.delays[0]  # exponential growth


def test_auth_error_falls_through_without_retry():
    misconfigured = ScriptedProvider("misconfigured", generation_script=[auth_error()])
    working = ScriptedProvider("working")
    engine = make_engine([misconfigured, working])
    result = engine.generate("q")
    assert result.provider == "working"
    assert misconfigured.generate_calls == 1  # never retried


def test_invalid_request_aborts_entire_chain():
    first = ScriptedProvider("first", generation_script=[invalid_request()])
    second = ScriptedProvider("second")
    engine = make_engine([first, second])
    with pytest.raises(InvalidRequestError):
        engine.generate("q")
    assert second.generate_calls == 0


def test_no_providers_available_raises_unavailable():
    engine = make_engine([ScriptedProvider("x", available=False)])
    with pytest.raises(ProviderUnavailableError):
        engine.generate("q")


def test_rate_limit_honors_retry_after():
    provider = ScriptedProvider("p", generation_script=[rate_limited(retry_after=7.5), "ok"])
    sleeper = NoDelaySleeper()
    engine = make_engine([provider], sleeper=sleeper, jitter=lambda _a: 0.0)
    result = engine.generate("q")
    assert result.text == "ok"
    assert sleeper.delays == [7.5]  # Retry-After wins over computed backoff


def test_rate_limit_without_header_uses_backoff():
    provider = ScriptedProvider("p", generation_script=[rate_limited(), "ok"])
    sleeper = NoDelaySleeper()
    engine = make_engine([provider], sleeper=sleeper, jitter=lambda _a: 0.25)
    engine.settings.llm_backoff_base_seconds = 0.5
    engine.generate("q")
    assert sleeper.delays[0] == pytest.approx(0.75)  # base + jitter


def test_all_providers_fail_reports_last_error():
    a = ScriptedProvider("a", generation_script=[transient("a-down")])
    b = ScriptedProvider("b", generation_script=[transient("b-down")])
    engine = make_engine([a, b], jitter=lambda _a: 0.0)
    with pytest.raises(ProviderUnavailableError) as excinfo:
        engine.generate("q")
    assert "b-down" in str(excinfo.value)


def test_engine_walks_providers_in_given_list_order():
    preferred = ScriptedProvider("preferred")
    backup = ScriptedProvider("backup")
    engine = make_engine([backup, preferred])
    assert engine.available_providers() == ["backup", "preferred"]
    assert engine.generate("q").provider == "backup"


# --------------------------------------------------------------------------
# Structured results / usage / cost
# --------------------------------------------------------------------------


def test_generate_result_is_typed_and_costed():
    provider = ScriptedProvider("p", generation_model="gpt-4o-mini")
    engine = make_engine([provider])
    result = engine.generate("hello")
    assert isinstance(result, GenerationResult)
    assert result.provider == "p"
    assert result.model == "gpt-4o-mini"
    assert result.usage is not None and result.usage.total_tokens == 15
    assert result.latency_ms >= 0.0
    # gpt-4o-mini: $0.15/1M input, $0.60/1M output -> 10*0.15/1e6 + 5*0.6/1e6
    assert result.cost_usd == pytest.approx((10 * 0.15 + 5 * 0.60) / 1_000_000)
    assert result.finish_reason == "stop"


def test_embed_result_is_typed_with_vectors_in_order():
    provider = ScriptedProvider("p")
    engine = make_engine([provider])
    results = engine.embed(["alpha", "beta"])
    assert [r.text_index for r in results] == [0, 1]
    assert results[0].vector == [5.0, 1.0, 0.5]  # len("alpha") seeded
    assert all(isinstance(r, EmbeddingResult) for r in results)


def test_embed_empty_input_short_circuits():
    provider = ScriptedProvider("p")
    engine = make_engine([provider])
    assert engine.embed([]) == []
    assert provider.embed_calls == 0


def test_embed_skips_chat_only_providers():
    chat_only = UnavailableEmbeddingProvider("chatonly")
    embedder = ScriptedProvider("embedder")
    engine = make_engine([chat_only, embedder])
    results = engine.embed(["text"])
    assert results[0].provider == "embedder"
    assert chat_only.embed_calls == 0
    assert engine.has_embedding() is True


def test_has_embedding_false_when_no_embedding_capable_provider():
    engine = make_engine([UnavailableEmbeddingProvider("chatonly")])
    assert engine.has_embedding() is False
    assert engine.has_generation() is True


# --------------------------------------------------------------------------
# Availability caching
# --------------------------------------------------------------------------


def test_availability_cached_within_ttl(clean_settings):
    provider = ScriptedProvider("p")
    settings = engine_settings(clean_settings, availability_ttl_seconds=999.0)
    engine = make_engine([provider], settings=settings)
    assert engine.available_providers() == ["p"]
    provider.available = False  # flip behind the cache's back
    assert engine.available_providers() == ["p"]  # cached
    assert engine.available_providers(refresh=True) == []  # forced probe
    engine.invalidate_availability()
    assert engine.available_providers() == []


def test_availability_probe_failure_counts_as_unavailable():
    class ExplodingProbe(ScriptedProvider):
        def is_available(self):
            raise RuntimeError("probe crashed")

    engine = make_engine([ExplodingProbe("explodey"), ScriptedProvider("ok")])
    assert engine.available_providers() == ["ok"]
    assert engine.has_generation() is True


# --------------------------------------------------------------------------
# OpenAI SDK integration shape (fake module — offline)
# --------------------------------------------------------------------------


class _FakeCompletions:
    def __init__(self, parent):
        self.parent = parent

    def create(self, **kwargs):
        self.parent.calls.append(kwargs)
        if self.parent.raise_on_create is not None:
            raise self.parent.raise_on_create
        return self.parent.chat_response


class _FakeEmbeddings:
    def __init__(self, parent):
        self.parent = parent

    def create(self, **kwargs):
        self.parent.embed_calls.append(kwargs)

        class _Resp:
            data = [
                types.SimpleNamespace(embedding=[0.1, 0.2], index=i)
                for i in range(len(kwargs["input"]))
            ]
            usage = types.SimpleNamespace(prompt_tokens=9, completion_tokens=0, total_tokens=9)

        return _Resp()


class FakeOpenAIClient:
    def __init__(self, chat_response=None):
        self.calls: list[dict] = []
        self.embed_calls: list[dict] = []
        self.raise_on_create: Exception | None = None
        self.constructor_kwargs: dict = {}
        self.chat_response = chat_response
        self.chat = types.SimpleNamespace(completions=_FakeCompletions(self))
        self.embeddings = _FakeEmbeddings(self)


def make_fake_openai_module(client: FakeOpenAIClient) -> Any:
    # types.ModuleType on purpose (it stands in for the real SDK module);
    # Any so tests can reach its dynamically attached attributes.
    module: Any = types.ModuleType("openai")

    def openai_constructor(**kwargs):
        client.constructor_kwargs = kwargs
        return client

    module.OpenAI = openai_constructor

    # Exception classes whose names drive classify_sdk_exception duck-typing.
    class APIStatusError(Exception):
        def __init__(self, message, response=None):
            super().__init__(message)
            self.response = response

    class AuthenticationError(APIStatusError): ...

    class RateLimitError(APIStatusError): ...

    class BadRequestError(APIStatusError): ...

    class APITimeoutError(APIStatusError): ...

    for cls in (
        APIStatusError,
        AuthenticationError,
        RateLimitError,
        BadRequestError,
        APITimeoutError,
    ):
        setattr(module, cls.__name__, cls)
    return module


def scripted_response(text="answer"):
    choice = types.SimpleNamespace(
        message=types.SimpleNamespace(content=text), finish_reason="stop"
    )
    usage = types.SimpleNamespace(prompt_tokens=12, completion_tokens=8, total_tokens=20)
    return types.SimpleNamespace(choices=[choice], usage=usage)


def provider_with_client(monkeypatch, client, clean_settings, **overrides):
    monkeypatch.setattr(openai_provider_module, "_openai", make_fake_openai_module(client))
    from llm_providers.openai_provider import OpenAIProvider

    settings = engine_settings(clean_settings)
    provider = OpenAIProvider(api_key="test-key", settings=settings, **overrides)
    return provider


def test_openai_generate_payload_and_result(monkeypatch, clean_settings):
    client = FakeOpenAIClient(chat_response=scripted_response("The answer"))
    provider = provider_with_client(monkeypatch, client, clean_settings)
    result = provider.generate("question", system="be brief", temperature=0.3)
    assert client.calls, "chat.completions.create was not called"
    call_kwargs = client.calls[0]
    assert call_kwargs["model"] == "gpt-4o-mini"
    assert call_kwargs["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "question"},
    ]
    assert call_kwargs["temperature"] == 0.3
    assert "response_format" not in call_kwargs
    assert result.text == "The answer"
    assert result.provider == "openai"


def test_openai_json_mode_sets_response_format(monkeypatch, clean_settings):
    client = FakeOpenAIClient(chat_response=scripted_response('{"query": "x"}'))
    provider = provider_with_client(monkeypatch, client, clean_settings)
    provider.generate("q", json_mode=True)
    assert client.calls[0]["response_format"] == {"type": "json_object"}


def test_openai_timeout_passed_to_client_and_request(monkeypatch, clean_settings):
    client = FakeOpenAIClient(chat_response=scripted_response())
    provider = provider_with_client(monkeypatch, client, clean_settings, timeout_seconds=12.5)
    _ = provider.client
    assert client.constructor_kwargs["timeout"] == 12.5
    assert provider.timeout_seconds == 12.5


def test_openai_auth_error_classified(monkeypatch, clean_settings):
    client = FakeOpenAIClient()
    fake_module = make_fake_openai_module(client)
    response = types.SimpleNamespace(status_code=401, headers={})
    client.raise_on_create = fake_module.AuthenticationError("bad key", response=response)
    provider = provider_with_client(monkeypatch, client, clean_settings)
    with pytest.raises(AuthenticationError):
        provider.generate("q")


def test_openai_bad_request_classified_non_retryable(monkeypatch, clean_settings):
    client = FakeOpenAIClient()
    fake_module = make_fake_openai_module(client)
    response = types.SimpleNamespace(status_code=400, headers={})
    client.raise_on_create = fake_module.BadRequestError("bad", response=response)
    provider = provider_with_client(monkeypatch, client, clean_settings)
    with pytest.raises(InvalidRequestError):
        provider.generate("q")


def test_openai_rate_limit_carries_retry_after(monkeypatch, clean_settings):
    client = FakeOpenAIClient()
    fake_module = make_fake_openai_module(client)
    response = types.SimpleNamespace(status_code=429, headers={"retry-after": "3"})
    client.raise_on_create = fake_module.RateLimitError("slow down", response=response)
    provider = provider_with_client(monkeypatch, client, clean_settings)
    with pytest.raises(RateLimitError) as excinfo:
        provider.generate("q")
    assert excinfo.value.retry_after == 3.0


def test_openai_embed_maps_results_by_index(monkeypatch, clean_settings):
    client = FakeOpenAIClient()
    provider = provider_with_client(monkeypatch, client, clean_settings)
    results = provider.embed(["a", "b"])
    assert client.embed_calls[0]["model"] == "text-embedding-3-small"
    assert len(results) == 2
    assert [r.vector for r in results] == [[0.1, 0.2], [0.1, 0.2]]
    assert results[1].text_index == 1
    assert results[0].usage.total_tokens == 9  # batch usage reported once


# --------------------------------------------------------------------------
# Ollama provider (fake requests session — loopback only)
# --------------------------------------------------------------------------


class FakeOllamaResponse:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._json = json_body or {}

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeOllamaSession:
    def __init__(self, get_response=None, post_responses=None, post_error=None):
        self.get_response = get_response
        self.post_responses = list(post_responses or [])
        self.post_error = post_error
        self.get_calls: list[tuple] = []
        self.post_calls: list[tuple] = []

    def get(self, url, timeout=None):
        self.get_calls.append((url, timeout))
        if isinstance(self.get_response, Exception):
            raise self.get_response
        return self.get_response or FakeOllamaResponse(status_code=200, json_body={"models": []})

    def post(self, url, json=None, timeout=None):  # noqa: A002
        self.post_calls.append((url, json, timeout))
        if self.post_error is not None:
            raise self.post_error
        if not self.post_responses:
            # Exhausted script (or get_response-only sessions): behave like a
            # daemon that went away mid-call rather than popping an empty list.
            raise requests.ConnectionError("refused")
        return self.post_responses.pop(0)


def ollama_provider(session, clean_settings, **overrides):
    from llm_providers.ollama_provider import OllamaProvider

    return OllamaProvider(settings=engine_settings(clean_settings), session=session, **overrides)


def test_ollama_available_when_daemon_responds(clean_settings):
    session = FakeOllamaSession(get_response=FakeOllamaResponse(200))
    assert ollama_provider(session, clean_settings).is_available() is True


def test_ollama_unavailable_when_daemon_down(clean_settings):
    import requests

    session = FakeOllamaSession(get_response=requests.ConnectionError("refused"))
    provider = ollama_provider(session, clean_settings)
    assert provider.is_available() is False
    with pytest.raises(TransientProviderError):
        provider.generate("q")


def test_ollama_generate_parses_content_and_usage(clean_settings):
    body = {
        "model": "llama3.1",
        "message": {"content": "local answer"},
        "done": True,
        "prompt_eval_count": 11,
        "eval_count": 7,
    }
    session = FakeOllamaSession(post_responses=[FakeOllamaResponse(200, body)])
    result = ollama_provider(session, clean_settings).generate("hi")
    url, payload, _timeout = session.post_calls[0]
    assert url.endswith("/api/chat")
    assert payload["stream"] is False
    assert payload["options"]["temperature"] == 0.2
    assert result.text == "local answer"
    assert result.provider == "ollama"
    assert result.cost_usd is None  # local model: never priced
    assert result.usage.total_tokens == 18


def test_ollama_model_missing_maps_to_invalid_request(clean_settings):
    session = FakeOllamaSession(post_responses=[FakeOllamaResponse(404, {"error": "not found"})])
    with pytest.raises(InvalidRequestError):
        ollama_provider(session, clean_settings).generate("hi")


def test_ollama_server_error_maps_to_transient(clean_settings):
    session = FakeOllamaSession(post_responses=[FakeOllamaResponse(503, {})])
    with pytest.raises(TransientProviderError):
        ollama_provider(session, clean_settings).generate("hi")


def test_ollama_embed_disabled_by_default(clean_settings):
    session = FakeOllamaSession()
    provider = ollama_provider(session, clean_settings)
    assert provider.supports_embeddings is False
    with pytest.raises(ProviderUnavailableError):
        provider.embed(["x"])


def test_ollama_embed_enabled_with_model(clean_settings):
    body = {"embeddings": [[0.1, 0.1]], "prompt_eval_count": 3}
    session = FakeOllamaSession(post_responses=[FakeOllamaResponse(200, body)])
    provider = ollama_provider(session, clean_settings, embedding_model="nomic-embed-text")
    assert provider.supports_embeddings is True
    results = provider.embed(["x"])
    assert results[0].vector == [0.1, 0.1]


def test_ollama_embed_vector_count_mismatch_is_transient(clean_settings):
    body = {"embeddings": [[0.1]]}
    session = FakeOllamaSession(post_responses=[FakeOllamaResponse(200, body)])
    provider = ollama_provider(session, clean_settings, embedding_model="m")
    with pytest.raises(TransientProviderError):
        provider.embed(["one", "two"])


# --------------------------------------------------------------------------
# Pricing helpers
# --------------------------------------------------------------------------


def test_price_lookup_prefix_match_and_unknown_none():
    assert price_for_model("gpt-4o-mini-2024-07-18") == (0.15, 0.60)
    assert price_for_model("text-embedding-3-small") == (0.02, 0.0)
    assert price_for_model("totally-unknown-model") is None


def test_estimate_cost_unknown_usage_or_model_is_none():
    assert estimate_cost_usd("gpt-4o-mini", None) is None
    assert estimate_cost_usd("mystery-model", TokenUsage(10, 10, 20)) is None
