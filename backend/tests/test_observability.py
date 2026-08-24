"""Phase 2 observability tests (offline).

Covers: metadata sanitization (secrets redacted, oversized values truncated),
NullTracer no-op behavior, LangfuseTracer against fake v2/v3 SDK clients
(injectable — the real langfuse package is never installed), get_tracer
degradation without credentials, the traced decorator, and engine->tracer
wiring.
"""

import pytest
from fakes import RecordingTracer, ScriptedProvider

from llm_providers.engine import LLMEngine
from observability.langfuse_wrapper import (
    NULL_TRACER,
    LangfuseTracer,
    NullTracer,
    Span,
    Trace,
    sanitize_metadata,
    traced,
)

# --------------------------------------------------------------------------
# Sanitization
# --------------------------------------------------------------------------


def test_secret_looking_keys_redacted():
    sanitized = sanitize_metadata(
        {
            "api_key": "sk-super-secret",
            "OPENAI_API_KEY": "sk-xyz",
            "authorization": "Bearer abc",
            "secret_key": "s3cr3t",
            "session_token": "tok",
            "safe": "value",
            "nested_dict_api_key_hint": "nope",
        }
    )
    assert sanitized["api_key"] == "<redacted>"
    assert sanitized["OPENAI_API_KEY"] == "<redacted>"
    assert sanitized["authorization"] == "<redacted>"
    assert sanitized["secret_key"] == "<redacted>"
    assert sanitized["session_token"] == "<redacted>"
    assert sanitized["nested_dict_api_key_hint"] == "<redacted>"
    assert sanitized["safe"] == "value"
    assert "sk-" not in str(sanitized)


def test_long_values_truncated_scalars_pass_through():
    long_text = "x" * 10_000
    sanitized = sanitize_metadata(
        {"text": long_text, "n": 5, "ratio": 0.5, "flag": True, "none": None}
    )
    assert len(sanitized["text"]) < 10_000 and sanitized["text"].endswith("[truncated]")
    assert sanitized["n"] == 5 and sanitized["ratio"] == 0.5
    assert sanitized["flag"] is True and sanitized["none"] is None


# --------------------------------------------------------------------------
# Null tracer: functional no-op
# --------------------------------------------------------------------------


def test_null_tracer_records_nothing_and_never_raises():
    trace = NULL_TRACER.start_trace("op", input={"question": "q"})
    with trace.span("step", detail="x") as span:
        span.metadata["more"] = "y"
    trace.finish(output={"status": "done"})
    assert trace.url is None


def test_null_tracer_span_captures_error_and_reraises():
    trace = NullTracer().start_trace("op")
    span = trace.span("step")
    with pytest.raises(ValueError), span:
        raise ValueError("application failure")
    assert span.error is not None and span.error.startswith("ValueError")


def test_null_tracer_flush_is_noop():
    NULL_TRACER.flush()  # must simply not raise


def test_base_span_finish_is_idempotent():
    finishes = []

    class CountingSpan(Span):
        def _finish(self):
            finishes.append(1)

    span = CountingSpan("s")
    span.finish()
    span.finish()
    assert len(finishes) == 1

    def broken_finish():
        raise RuntimeError("tracer client down")

    class BrokenSpan(Span):
        _finish = broken_finish

    resilient = BrokenSpan("b")
    resilient.finish()  # must not raise despite tracer client failure
    assert resilient._finished is True


# --------------------------------------------------------------------------
# Recording tracer: what engine/chains actually emit
# --------------------------------------------------------------------------


def test_recording_tracer_happy_path():
    tracer = RecordingTracer(url="mem://trace/1")
    trace = tracer.start_trace("rag_query", input={"question": "q"})
    with trace.span("retrieve", top_k=6) as span:
        span.metadata["hits"] = 3
    trace.finish(output={"citations": 2})
    assert trace.url == "mem://trace/1"
    names = [record.name for record in tracer.records]
    assert names == ["retrieve"]
    assert tracer.records[0].metadata["top_k"] == 6
    assert tracer.finished[-1]["output"] == {"citations": 2}


def test_engine_emits_llm_spans_with_provider_and_model():
    provider = ScriptedProvider("p", generation_model="gpt-4o-mini", embedding_model="emb-1")
    tracer = RecordingTracer()
    engine = LLMEngine(providers=[provider], tracer=tracer, jitter=lambda _a: 0.0)
    engine.generate("hello")
    engine.embed(["text"])
    generate_spans = [r for r in tracer.records if r.name == "generate.attempt"]
    embed_spans = [r for r in tracer.records if r.name == "embed.attempt"]
    assert generate_spans[0].metadata["provider"] == "p"
    assert generate_spans[0].metadata["attempt"] == 1
    assert generate_spans[0].metadata["model"] == "gpt-4o-mini"
    assert generate_spans[0].metadata["usage_total_tokens"] == 15
    assert embed_spans[0].metadata["provider"] == "p"


def test_engine_records_retry_error_on_span():
    from fakes import transient

    provider = ScriptedProvider("flaky", generation_script=[transient("down"), "ok"])
    tracer = RecordingTracer()
    engine = LLMEngine(
        providers=[provider], tracer=tracer, sleeper=lambda _s: None, jitter=lambda _a: 0.0
    )
    result = engine.generate("q")
    assert result.text == "ok"
    errored = [r for r in tracer.records if r.error]
    assert len(errored) == 1
    assert errored[0].error is not None and "TransientProviderError" in errored[0].error
    assert errored[0].metadata["retry_delay_s"] >= 0


# --------------------------------------------------------------------------
# LangfuseTracer against fake SDK shapes (real package never installed here)
# --------------------------------------------------------------------------


class FakeV2Trace:
    def __init__(self, log):
        self.log = log
        self.spans = []

    def span(self, name, input=None, metadata=None):  # noqa: A002
        span = FakeV2Span(name, metadata, self.log)
        self.spans.append(span)
        return span

    def update(self, output=None):
        self.log.append(("trace.update", output))

    def get_trace_url(self):
        return "https://langfuse.test/trace/v2-url"


class FakeV2Span:
    def __init__(self, name, metadata, log):
        self.name = name
        self.log = log
        self.updates = []
        self.ended = False

    def update(self, **kwargs):
        self.updates.append(kwargs)

    def end(self):
        self.ended = True


class FakeV2Client:
    def __init__(self):
        self.log = []
        self.traces = []

    def trace(self, id=None, name=None, input=None, metadata=None):  # noqa: A002
        self.traces.append({"id": id, "name": name, "input": input, "metadata": metadata})
        trace = FakeV2Trace(self.log)
        self.log.append(("trace.created", name))
        return trace

    def flush(self):
        self.log.append(("flush",))


class FakeV3RootSpan:
    def __init__(self, name, log):
        self.name = name
        self.log = log
        self.children = []

    def span(self, name, metadata=None):  # noqa: A002
        child = FakeV2Span(name, metadata, self.log)
        self.children.append(child)
        return child

    def end(self):
        self.log.append(("root.end",))

    def get_trace_url(self):
        return "https://langfuse.test/trace/v3-url"


class FakeV3Client:
    """v3 shape: start_span instead of trace(); no .trace attribute."""

    def __init__(self):
        self.log = []
        self.roots = []

    def start_span(self, name, trace_id=None):
        root = FakeV3RootSpan(name, self.log)
        self.roots.append((name, trace_id))
        return root

    def flush(self):
        self.log.append(("flush",))


def test_langfuse_tracer_v2_shape():
    client = FakeV2Client()
    tracer = LangfuseTracer(client, host="https://langfuse.test")
    trace = tracer.start_trace(
        "llm.generate", input={"prompt": "p" * 9000}, metadata={"api_key": "sk-nope"}
    )
    with trace.span("attempt", attempt=1):
        pass
    trace.finish(output={"status": "answered"})

    created = client.traces[0]
    assert created["name"] == "llm.generate"
    assert len(created["input"]["prompt"]) < 9000  # truncated by sanitizer
    assert created["metadata"]["api_key"] == "<redacted>"
    # span lifecycle: update + end both fired on the SDK object
    v2_trace = client.log  # ordering includes trace.created first
    assert v2_trace[0] == ("trace.created", "llm.generate")
    assert trace.url == "https://langfuse.test/trace/v2-url"  # SDK URL preferred
    tracer.flush()
    assert ("flush",) in client.log


def test_langfuse_tracer_v3_shape():
    client = FakeV3Client()
    tracer = LangfuseTracer(client, host="https://langfuse.test")
    trace = tracer.start_trace("ingest", input={"source": "sec"})
    with trace.span("chunk"):
        pass
    trace.finish(output={"chunks": 5})
    assert trace.url == "https://langfuse.test/trace/v3-url"
    assert client.roots[0][0] == "ingest"
    assert client.roots[0][1]  # trace_id passed through
    assert client.log[-1] == ("root.end",)  # finish ends the v3 root span


class PlainNoUrlTrace:
    """v2-ish trace object exposing no get_trace_url helper."""

    def __init__(self, log: list):
        self.log = log

    def span(self, name, input=None, metadata=None):  # noqa: A002
        return FakeV2Span(name, metadata, self.log)

    def update(self, output=None):
        self.log.append(("trace.update", output))


class PlainV2Client:
    """v2-shaped client whose traces lack the SDK URL helper."""

    def __init__(self):
        self.log: list = []

    def trace(self, id=None, name=None, input=None, metadata=None):  # noqa: A002
        return PlainNoUrlTrace(self.log)

    def flush(self):
        self.log.append(("flush",))


def test_langfuse_tracer_manual_url_fallback():
    tracer = LangfuseTracer(PlainV2Client(), host="https://lf.example")
    trace = tracer.start_trace("q")
    assert trace.url is not None
    assert trace.url.startswith("https://lf.example/trace/")


def test_tracer_client_failures_never_break_request_path():
    class ExplodingClient:
        def trace(self, **_):
            raise RuntimeError("sdk exploded")

    tracer = LangfuseTracer(ExplodingClient())
    trace = tracer.start_trace("op")  # degrades to an inert Trace instead of raising
    assert isinstance(trace, Trace)
    assert trace.url is None
    with trace.span("step"):
        pass  # inert spans work; request path unaffected


def test_span_finish_failure_swallowed():
    from observability.langfuse_wrapper import LangfuseSpan

    class FlakyEndSpan:
        def update(self, **_):
            return None

        def end(self):
            raise RuntimeError("network hiccup at flush time")

    langfuse_span = LangfuseSpan(FlakyEndSpan(), "s", {})
    langfuse_span.finish()  # must not raise despite the SDK failing to end
    assert langfuse_span._finished is True


# --------------------------------------------------------------------------
# get_tracer wiring
# --------------------------------------------------------------------------


def test_get_tracer_returns_null_without_credentials(clean_settings):
    from observability.langfuse_wrapper import get_tracer

    settings = clean_settings(langfuse_public_key=None, langfuse_secret_key=None)
    assert isinstance(get_tracer(settings), NullTracer)


def test_get_tracer_degrades_when_package_missing(clean_settings, monkeypatch):
    import builtins

    from config.settings import Settings
    from observability.langfuse_wrapper import get_tracer

    settings = Settings(
        _env_file=None,
        langfuse_public_key="pk",
        langfuse_secret_key="sk",
        langfuse_host="https://cloud.langfuse.com",
    )
    # langfuse is intentionally not installed in this environment; simulate the
    # import failing anyway so the test holds even if it gets installed later.
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "langfuse" or name.startswith("langfuse."):
            raise ImportError("No module named 'langfuse'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert isinstance(get_tracer(settings), NullTracer)


# --------------------------------------------------------------------------
# Decorator
# --------------------------------------------------------------------------


def test_traced_decorator_records_result_and_kwargs():
    tracer = RecordingTracer()

    @traced("agent.step", tracer=tracer)
    def work(ticker: str, api_key: str = "sk-hidden"):
        return {"ticker": ticker}

    # api_key passed explicitly: the decorator sanitizes keyword arguments
    # (secret-looking keys redacted before anything is exported).
    result = work("AAPL", api_key="sk-hidden")
    assert result == {"ticker": "AAPL"}
    record = tracer.records[0]
    assert record.name == "call"
    assert record.metadata["function"] == "work"
    assert record.metadata["api_key"] == "<redacted>"  # secrets never exported
    assert record.metadata["result"] == {"ticker": "AAPL"}


def test_traced_decorator_reraises_after_recording():
    tracer = RecordingTracer()

    @traced("bad.step", tracer=tracer)
    def work():
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        work()
    assert tracer.records[0].error is not None
