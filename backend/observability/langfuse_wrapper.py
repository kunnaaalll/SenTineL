"""Langfuse tracing wrapper (spec section 11).

A tiny tracer abstraction the rest of Sentinel programs against:

- `Tracer.start_trace(name, input=..., metadata=...)` -> `Trace`
- `Trace.span(name, **metadata)` — context manager for a child span
- `Trace.finish(output=...)` closes the trace; `Trace.url` links to it

Two implementations:

- `NullTracer` — zero-op default used whenever Langfuse credentials are absent
  or the langfuse package isn't installed. Same interface, records nothing,
  `url` stays None. Nothing anywhere branches on "is tracing enabled".
- `LangfuseTracer` — adapts to both the v2 (`client.trace(...)` / `trace.span`)
  and v3 (`client.start_span`) SDK shapes via feature detection. The client is
  injectable so tests exercise this class with fakes, never the real SDK.

Safety rules enforced here (not left to call sites):

- Secrets never reach Langfuse: metadata keys matching api_key/token/secret/
  password/authorization are redacted by `sanitize_metadata`, long values are
  truncated so full prompts/documents aren't shipped wholesale.
- Tracing failures must not break the request path: span bookkeeping re-raises
  application exceptions after recording them, but tracer client errors are
  swallowed with a warning.

Usage:
    tracer = get_tracer(settings)          # NullTracer without creds
    trace = tracer.start_trace("rag_query", input={"question": q})
    with trace.span("retrieve", top_k=k):
        ...
    trace.finish(output={"citations": n})
    result.trace_url = trace.url
"""

import logging
import re
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# Any metadata key containing one of these fragments is redacted before export.
_SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|secret|token|password|authorization|credential|cookie)", re.IGNORECASE
)
_MAX_VALUE_CHARS = 4000


def sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Copy metadata safe for export: secret-looking keys redacted, oversized
    string values truncated. Scalars pass through; everything else is str()'d."""
    if not metadata:
        return {}
    out: dict[str, Any] = {}
    for key, value in metadata.items():
        if _SECRET_KEY_PATTERN.search(str(key)):
            out[str(key)] = "<redacted>"
            continue
        if value is None or isinstance(value, (bool, int, float)):
            out[str(key)] = value
            continue
        text = value if isinstance(value, str) else str(value)
        if len(text) > _MAX_VALUE_CHARS:
            text = text[:_MAX_VALUE_CHARS] + "…[truncated]"
        out[str(key)] = text
    return out


@dataclass
class SpanRecord:
    """What in-memory tracers/tests capture per span."""

    name: str
    latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


# --------------------------------------------------------------------------
# Base span/trace: timing, error capture, never-break-the-request guarantees
# --------------------------------------------------------------------------


class Span:
    """Span usable both as a context manager and with explicit finish() (for
    engines that manage retry loops manually). Concrete tracers hook `_finish`;
    application exceptions are recorded then re-raised; tracer client failures
    never propagate into the request path."""

    def __init__(self, name: str, metadata: dict[str, Any] | None = None):
        self.name = name
        self.metadata = sanitize_metadata(metadata)
        self.error: str | None = None
        self.output: dict[str, Any] | None = None
        self._started = time.perf_counter()
        self._finished = False

    @property
    def latency_ms(self) -> float:
        return (time.perf_counter() - self._started) * 1000.0

    def record_error(self, message: str) -> None:
        self.error = message

    def finish(self) -> None:
        """Close the span exactly once; tracer client failures are swallowed."""
        if self._finished:
            return
        self._finished = True
        try:
            self._finish()
        except Exception:  # noqa: BLE001 — observability must not break requests
            logger.warning("Tracer failed to finish span %r", self.name, exc_info=True)

    def __enter__(self) -> "Span":
        return self

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:
        if exc is not None:
            self.record_error(f"{exc_type.__name__}: {exc}")
        self.finish()
        return False  # never suppress the application exception

    def _finish(self) -> None:
        """Hook for concrete tracers."""


class Trace:
    """A root trace holding child spans. The default implementation is inert
    (NullTracer's trace): spans time themselves but export nowhere."""

    def __init__(self, name: str, input_data: dict[str, Any] | None = None):
        self.name = name
        self.input = sanitize_metadata(input_data)
        self.spans: list[Span] = []

    @property
    def url(self) -> str | None:
        return None

    def span(self, name: str, **metadata: Any) -> Span:
        child = Span(name, metadata)
        self.spans.append(child)
        return child

    def finish(self, output: dict[str, Any] | None = None) -> None:
        """Hook for concrete tracers."""


class Tracer(ABC):
    """Root abstraction. `start_trace` opens a trace; spans hang off it."""

    @abstractmethod
    def start_trace(
        self,
        name: str,
        *,
        input: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Trace: ...

    def flush(self) -> None:  # noqa: B027 — deliberately optional hook
        """Best-effort delivery nudge; no-op by default."""


class NullTracer(Tracer):
    """Default when Langfuse isn't configured. Fully functional interface,
    records nothing, `url` always None."""

    def start_trace(
        self,
        name: str,
        *,
        input: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Trace:
        return Trace(name, input)

    def flush(self) -> None:
        return None


NULL_TRACER = NullTracer()


# --------------------------------------------------------------------------
# Langfuse implementation (v2 + v3 SDK shapes, client injectable for tests)
# --------------------------------------------------------------------------


def _trace_url(host: str | None, trace_id: str, *candidates: Any) -> str | None:
    """Prefer an SDK-provided URL; fall back to constructing one from host+id."""
    for candidate in candidates:
        getter = getattr(candidate, "get_trace_url", None)
        if callable(getter):
            try:
                url = getter()
                if url:
                    return str(url)
            except Exception:  # noqa: BLE001
                pass
    if host and trace_id:
        return f"{host.rstrip('/')}/trace/{trace_id}"
    return None


class LangfuseSpan(Span):
    def __init__(self, sdk_span: Any, name: str, metadata: dict[str, Any] | None):
        super().__init__(name, metadata)
        self._sdk_span = sdk_span

    def _finish(self) -> None:
        payload: dict[str, Any] = {"metadata": {**self.metadata}}
        if self.error:
            payload["status_message"] = self.error
        update = getattr(self._sdk_span, "update", None)
        end = getattr(self._sdk_span, "end", None)
        if callable(update):
            update(**payload)
        if callable(end):
            end()


class LangfuseTrace(Trace):
    """v2-style root: created via client.trace(id=..., name=...), children via
    trace.span(...). Works unchanged on v3 clients that still expose .trace."""

    def __init__(
        self,
        sdk_trace: Any,
        *,
        name: str,
        input_data: dict[str, Any] | None,
        trace_id: str,
        host: str | None,
    ):
        super().__init__(name, input_data)
        self._sdk_trace = sdk_trace
        self._trace_id = trace_id
        self._host = host

    @property
    def url(self) -> str | None:
        return _trace_url(self._host, self._trace_id, self._sdk_trace)

    def span(self, name: str, **metadata: Any) -> Span:
        sdk_span = self._sdk_trace.span(name=name, input=None, metadata=sanitize_metadata(metadata))
        child = LangfuseSpan(sdk_span, name, metadata)
        self.spans.append(child)
        return child

    def finish(self, output: dict[str, Any] | None = None) -> None:
        update = getattr(self._sdk_trace, "update", None)
        if callable(update):
            update(output=sanitize_metadata(output))


class V3RootSpanTrace(LangfuseTrace):
    """v3-style root: client.start_span(...) returns a root span; children come
    from root.span(...). URL resolution prefers the SDK's own helper, and the
    root span must be ended explicitly when the trace finishes."""

    def span(self, name: str, **metadata: Any) -> Span:
        sdk_span = self._sdk_trace.span(name=name, metadata=sanitize_metadata(metadata))
        child = LangfuseSpan(sdk_span, name, metadata)
        self.spans.append(child)
        return child

    def finish(self, output: dict[str, Any] | None = None) -> None:
        super().finish(output)
        try:
            end = getattr(self._sdk_trace, "end", None)
            if callable(end):
                end()
        except Exception:  # noqa: BLE001 — observability must not break requests
            logger.warning("Failed to end v3 root span", exc_info=True)


class LangfuseTracer(Tracer):
    """Wraps a configured Langfuse client. `client` is injectable — tests pass
    fakes shaped like either SDK generation; production uses get_tracer()."""

    def __init__(self, client: Any, *, host: str | None = None):
        self._client = client
        self._host = host

    def start_trace(
        self,
        name: str,
        *,
        input: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Trace:
        trace_id = uuid.uuid4().hex
        try:
            start_span = getattr(self._client, "start_span", None)
            if callable(start_span) and not hasattr(self._client, "trace"):
                root = start_span(name=name, trace_id=trace_id)
                return V3RootSpanTrace(
                    root, name=name, input_data=input, trace_id=trace_id, host=self._host
                )
            sdk_trace = self._client.trace(
                id=trace_id,
                name=name,
                input=sanitize_metadata(input),
                metadata=sanitize_metadata(metadata),
            )
            return LangfuseTrace(
                sdk_trace, name=name, input_data=input, trace_id=trace_id, host=self._host
            )
        except Exception:  # noqa: BLE001 — a broken tracer never breaks a request
            logger.warning("Langfuse trace creation failed; continuing untraced", exc_info=True)
            return Trace(name, input)

    def flush(self) -> None:
        try:
            flush = getattr(self._client, "flush", None)
            if callable(flush):
                flush()
        except Exception:  # noqa: BLE001
            logger.warning("Langfuse flush failed", exc_info=True)


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


def build_langfuse_client(settings: Settings) -> Any | None:
    """Import and construct the Langfuse client, or None when unconfigured /
    package missing / construction fails. Never raises."""
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    try:
        import langfuse  # deferred optional dependency

        return langfuse.Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except Exception:  # noqa: BLE001 — any failure means "run without tracing"
        logger.warning("Langfuse unavailable (%s); tracing disabled", settings.langfuse_host)
        return None


def get_tracer(settings: Settings | None = None) -> Tracer:
    """Configured LangfuseTracer when credentials + package exist, else the
    shared NULL_TRACER. Safe to call at startup — makes no network calls."""
    settings = settings or get_settings()
    client = build_langfuse_client(settings)
    if client is None:
        return NULL_TRACER
    return LangfuseTracer(client, host=settings.langfuse_host)


# --------------------------------------------------------------------------
# Decorator convenience (agent nodes / ad-hoc functions)
# --------------------------------------------------------------------------


def _summarize(value: Any) -> Any:
    """Compact, secret-safe description of a call argument or return value.
    Scalars and JSON-ish containers pass through; long strings are truncated;
    anything else falls back to a truncated repr."""
    if value is None or isinstance(value, (bool, int, float, dict, list)):
        return value
    text = value if isinstance(value, str) else repr(value)
    if len(text) > 200:
        text = text[:200] + "…"
    return text


def traced(name: str, *, tracer: Tracer | None = None):
    """Decorator wrapping sync functions in a span named `name`.

    Positional args are summarized by type/shape only; keyword arguments are
    sanitized (secret-looking keys redacted). Application exceptions pass
    through after being recorded on the span.
    """

    def decorator(fn):
        def wrapper(*args, **kwargs):
            active = tracer if tracer is not None else NULL_TRACER
            # Span.__init__ sanitizes: secret-looking keys redacted, long values truncated.
            metadata = {"function": getattr(fn, "__name__", str(fn)), **kwargs}
            with active.start_trace(name).span("call", **metadata) as span:
                result = fn(*args, **kwargs)
                span.metadata["result"] = _summarize(result)
                return result

        return wrapper

    return decorator
