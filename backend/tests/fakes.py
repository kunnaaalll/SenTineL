"""Shared offline fakes for the Phase 2 test suite.

Everything here is deterministic and network-free: no OpenAI, no Ollama,
no Pinecone, no Langfuse, no SEC, no news APIs. Tests compose these fakes;
the real adapters/stores stay covered by the Phase 1 suite's own fakes.
"""

import math
from typing import Any

from data_sources.base import DataSourceAdapter
from llm_providers.base import (
    AuthenticationError,
    BaseProvider,
    EmbeddingResult,
    GenerationResult,
    InvalidRequestError,
    RateLimitError,
    TokenUsage,
    TransientProviderError,
)
from models.schemas import Chunk, RawDocument, RetrievedChunk
from observability.langfuse_wrapper import Span, SpanRecord, Trace, Tracer
from retrieval.base import VectorStore
from retrieval.pinecone_store import fit_metadata, to_metadata

# --------------------------------------------------------------------------
# Providers: scripted behavior for engine tests
# --------------------------------------------------------------------------


class ScriptedProvider(BaseProvider):
    """Provider whose calls run a scripted list of outcomes.

    Each outcome is an Exception instance (raised) or a string (returned as
    GenerationResult text / EmbeddingResult vector seed). Scripts repeat the
    last entry once exhausted so "succeeds on attempt N" is easy to express:
        ScriptedProvider("p", generate_script=[TransientProviderError("t"), "ok"])
    """

    name = "scripted"

    def __init__(
        self,
        name: str = "scripted",
        *,
        available: bool = True,
        supports_embeddings: bool = True,
        generation_model: str = "script-gen",
        embedding_model: str = "script-embed",
        generation_script: list | None = None,
        embed_script: list | None = None,
    ):
        self.name = name
        self.available = available
        self.supports_embeddings = supports_embeddings
        self.generation_model = generation_model
        self.embedding_model = embedding_model
        self.generation_script = list(generation_script or ["ok"])
        self.embed_script = list(embed_script or ["vec"])
        self.generate_calls = 0
        self.embed_calls = 0

    def is_available(self) -> bool:
        return self.available

    def _next(self, script: list, counter: int) -> Any:
        index = min(counter, len(script) - 1)
        return script[index]

    def generate(self, prompt, *, system=None, temperature=0.2, max_tokens=None, json_mode=False):
        outcome = self._next(self.generation_script, self.generate_calls)
        self.generate_calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return GenerationResult(
            text=str(outcome),
            provider=self.name,
            model=self.generation_model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            finish_reason="stop",
        )

    def embed(self, texts):
        outcome = self._next(self.embed_script, self.embed_calls)
        self.embed_calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return [
            EmbeddingResult(
                vector=[float(len(text)), 1.0, 0.5],
                text_index=index,
                provider=self.name,
                model=self.embedding_model,
            )
            for index, text in enumerate(texts)
        ]


class UnavailableEmbeddingProvider(ScriptedProvider):
    """Chat-capable but embeddings disabled (mirrors default Ollama config)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, supports_embeddings=False, **kwargs)


# Convenience error constructors used across test files.


def transient(msg="timeout") -> TransientProviderError:
    return TransientProviderError(msg)


def rate_limited(retry_after=None) -> RateLimitError:
    return RateLimitError("rate limited", retry_after=retry_after)


def auth_error() -> AuthenticationError:
    return AuthenticationError("bad key")


def invalid_request() -> InvalidRequestError:
    return InvalidRequestError("bad parameter")


# --------------------------------------------------------------------------
# Vector store: in-memory, source-delete aware
# --------------------------------------------------------------------------


class FakeVectorStore(VectorStore):
    """VectorStore double storing full metadata in memory.

    Vectors are keyed by chunk_id (upsert semantics like Pinecone), and
    delete_source removes every vector whose stored source_id matches —
    including stale ids seeded directly via `seed`, which is how tests
    simulate orphaned vectors from earlier ingestion runs.
    """

    def __init__(self, *, ready: bool = True):
        self.vectors: dict[str, tuple[list[float], dict]] = {}
        self.ready_flag = ready
        self.delete_calls: list[str] = []
        self.add_calls: list[tuple[list[Chunk], list[list[float]]]] = []

    def is_ready(self) -> bool:
        return self.ready_flag

    def add(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        assert len(chunks) == len(vectors)
        self.add_calls.append((list(chunks), [list(v) for v in vectors]))
        for chunk, vector in zip(chunks, vectors, strict=True):
            # Mirror PineconeVectorStore.add: persist the flattened, size-capped
            # metadata view (adds source_id/source_type/text/entities), so
            # assertions see exactly what live retrieval would.
            fitted, _ = fit_metadata(to_metadata(chunk))
            self.vectors[chunk.chunk_id] = (vector, fitted)

    def delete_source(self, source_id: str) -> None:
        self.delete_calls.append(source_id)
        self.vectors = {
            cid: (v, meta)
            for cid, (v, meta) in self.vectors.items()
            if meta.get("source_id") != source_id
        }

    def search(self, query_vector, top_k=5, filters=None):
        scored: list[RetrievedChunk] = []
        for cid, (vector, meta) in self.vectors.items():
            if filters and not _matches(filters, meta):
                continue
            chunk = RetrievedChunk(
                chunk_id=cid,
                source_id=str(meta.get("source_id", "")),
                source_type=str(meta.get("source_type", "")),
                section=meta.get("section"),
                page_or_position=str(meta.get("page_or_position", "")),
                text=str(meta.get("text", "")),
                entities=list(meta.get("entities", [])),
                metadata=dict(meta),
                score=_cosine(query_vector, vector),
            )
            scored.append(chunk)
        scored.sort(key=lambda c: -c.score)
        return scored[:top_k]

    def seed_stale_vector(self, chunk_id: str, source_id: str) -> None:
        """Plant a vector as an earlier ingestion run would have left it."""
        self.vectors[chunk_id] = ([0.0, 0.0, 1.0], {"source_id": source_id, "text": "stale"})


def _matches(filters: dict, meta: dict) -> bool:
    if filters.get("ticker"):
        meta_ticker = meta.get("ticker")
        if not meta_ticker and str(meta.get("source_id", "")).startswith("SEC:"):
            parts = str(meta.get("source_id", "")).split(":")
            if len(parts) >= 2:
                meta_ticker = parts[1]
        if meta_ticker != filters["ticker"] and filters["ticker"] not in meta.get("entities", []):
            return False
    if filters.get("source_type") and meta.get("source_type") != filters["source_type"]:
        return False
    date_range = filters.get("date_range")
    if date_range:
        value = str(meta.get("published_date") or meta.get("date") or "")
        start, end = date_range
        if start and (not value or value < start):
            return False
        if end and (not value or value > end):
            return False
    return True


def _cosine(a: list[float], b: list[float]) -> float:
    # Dimension-tolerant on purpose: scripted providers and hand-seeded stores
    # don't always agree on vector width, and score fidelity isn't what the
    # fakes are testing.
    dot = sum(x * y for x, y in zip(a, b))  # noqa: B905 — width mismatch is fine here
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


# --------------------------------------------------------------------------
# Data source adapter: canned documents
# --------------------------------------------------------------------------


class FakeAdapter(DataSourceAdapter):
    def __init__(
        self,
        documents: list[RawDocument] | None = None,
        *,
        available: bool = True,
        name: str = "fake_source",
    ):
        self.name = name
        self.documents = documents or []
        self.available = available
        self.fetch_calls: list[dict] = []

    def is_available(self) -> bool:
        return self.available

    def fetch(self, query_params: dict) -> list[RawDocument]:
        self.fetch_calls.append(dict(query_params))
        return list(self.documents)


# --------------------------------------------------------------------------
# Tracer: records spans in memory
# --------------------------------------------------------------------------


class RecordingSpan(Span):
    """Span that mirrors its state into a SpanRecord for assertions. The
    record's metadata dict is shared with the span so post-construction
    writes (e.g. result summaries) are visible to tests."""

    def __init__(self, name: str, metadata: dict[str, Any] | None = None):
        super().__init__(name, metadata)
        self.record = SpanRecord(name=name, metadata=self.metadata)

    def record_error(self, message: str) -> None:
        super().record_error(message)
        self.record.error = message

    def finish(self) -> None:
        self.record.latency_ms = round(self.latency_ms, 3)
        super().finish()


class RecordingTrace(Trace):
    """Trace whose spans are recorded on the owning tracer."""

    def __init__(
        self,
        owner: "RecordingTracer",
        name: str,
        input_data: dict[str, Any] | None,
        url_value: str | None,
    ):
        super().__init__(name, input_data)
        self._owner = owner
        self._url_value = url_value

    @property
    def url(self) -> str | None:
        return self._url_value

    def span(self, name: str, **metadata: Any) -> RecordingSpan:
        child = RecordingSpan(name, metadata)
        self.spans.append(child)
        self._owner.records.append(child.record)
        return child

    def finish(self, output: dict[str, Any] | None = None) -> None:
        self._owner.finished.append({"name": self.name, "output": output})


class RecordingTracer(Tracer):
    """In-memory tracer exposing everything Sentinel writes to spans."""

    def __init__(self, url: str | None = "mem://trace/abc"):
        self.records: list[SpanRecord] = []
        self.traces: list[dict[str, Any]] = []
        self.finished: list[dict[str, Any]] = []
        self.url_value = url

    def start_trace(
        self,
        name: str,
        *,
        input: dict[str, Any] | None = None,  # noqa: A002
        metadata: dict[str, Any] | None = None,
    ) -> Trace:
        trace = RecordingTrace(self, name, input, self.url_value)
        self.traces.append({"name": name, "input": input})
        return trace


__all__ = [
    "FakeAdapter",
    "FakeVectorStore",
    "RecordingSpan",
    "RecordingTrace",
    "RecordingTracer",
    "ScriptedProvider",
    "UnavailableEmbeddingProvider",
    "auth_error",
    "invalid_request",
    "rate_limited",
    "transient",
]
