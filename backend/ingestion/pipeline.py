"""Ingestion pipeline (spec section 7): source -> chunk -> extract -> embed -> store.

Orchestrates the Phase 1 pieces behind one idempotent entry point:

1. Fetch RawDocuments through a DataSourceAdapter (never a concrete source).
2. Chunk with the financial chunker (tables atomic, footnotes as metadata).
3. Tag entities: deterministic regex pass always; LLM-assisted pass only when
   settings.enable_llm_entity_extraction is on and the engine can generate.
4. Embed chunks through the LLMEngine (batched).
5. Store through the VectorStore interface.

Idempotency & consistency:
- Deterministic chunk ids (financial_chunker) make unchanged documents upsert
  over the same vector keys.
- delete-before-reingest (settings.delete_before_reingest, on by default)
  removes all vectors of a source_id first, so re-ingesting a revised filing
  never leaves orphaned vectors from shifted chunk boundaries (audit risk #2).

Failure semantics: per-document isolation — one document failing (embedding
error, store error) is recorded in the report's `failures` list and remaining
documents continue. The report (IngestionStats) always comes back; nothing is
raised for document-level problems.

Provenance: every chunk carries source_id / source_type / ticker / title /
url via the chunker's metadata flow, so citations map back to real documents.
"""

import logging
import time
from dataclasses import dataclass, field

from config.settings import Settings, get_settings
from data_sources.base import DataSourceAdapter
from ingestion.entity_extractor import enrich_chunk, extract_entities_llm
from ingestion.financial_chunker import chunk_document
from llm_providers.base import EmbeddingResult
from llm_providers.engine import LLMEngine
from models.schemas import Chunk, RawDocument
from observability.langfuse_wrapper import NULL_TRACER, Tracer
from retrieval.base import VectorStore
from retrieval.pinecone_store import fit_metadata, to_metadata

logger = logging.getLogger(__name__)


@dataclass
class IngestionFailure:
    """One document (or the whole fetch) that could not be ingested."""

    source_id: str
    stage: str  # "fetch" | "delete" | "chunk" | "embed" | "store"
    error: str


@dataclass
class IngestionStats:
    """What one ingest() run did — returned even on partial failure."""

    documents_fetched: int = 0
    documents_ingested: int = 0
    chunks_indexed: int = 0
    chunks_truncated_for_metadata: int = 0
    documents_failed: int = 0
    failures: list[IngestionFailure] = field(default_factory=list)
    embedding_provider: str | None = None
    embedding_model: str | None = None
    duration_ms: float = 0.0
    deleted_sources: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.documents_failed == 0


class IngestionPipeline:
    def __init__(
        self,
        *,
        adapters: dict[str, DataSourceAdapter],
        engine: LLMEngine,
        store: VectorStore,
        settings: Settings | None = None,
        tracer: Tracer | None = None,
    ):
        self.adapters = adapters
        self.engine = engine
        self.store = store
        self.settings = settings or get_settings()
        self.tracer = tracer if tracer is not None else NULL_TRACER

    # -- public entry point -----------------------------------------------------

    def ingest(
        self,
        query_params: dict,
        *,
        source_type: str = "sec_filing",
        delete_existing: bool | None = None,
    ) -> IngestionStats:
        """Fetch + index documents. Returns full statistics; document-level
        errors land in stats.failures instead of raising."""
        started = time.perf_counter()
        stats = IngestionStats()
        trace = self.tracer.start_trace("ingest", input={"source_type": source_type})

        adapter = self._resolve_adapter(source_type)
        try:
            documents = adapter.fetch(query_params)
        except Exception as exc:  # noqa: BLE001 — fetch failure is reported, not fatal
            logger.exception("Fetch failed for adapter %s", adapter.name)
            stats.failures.append(IngestionFailure("*", "fetch", f"{type(exc).__name__}: {exc}"))
            stats.documents_failed = len(stats.failures)
            stats.duration_ms = (time.perf_counter() - started) * 1000
            trace.finish(output={"status": "fetch_failed"})
            return stats

        stats.documents_fetched = len(documents)
        remove_first = (
            self.settings.delete_before_reingest if delete_existing is None else delete_existing
        )

        for doc in documents:
            try:
                indexed = self._ingest_document(doc, stats, delete=remove_first)
            except Exception as exc:  # noqa: BLE001 — isolate per-document failures
                logger.exception("Ingestion failed for %s", doc.source_id)
                stage = _failure_stage(exc)
                stats.failures.append(
                    IngestionFailure(doc.source_id, stage, f"{type(exc).__name__}: {exc}")
                )
                continue
            if indexed:
                stats.documents_ingested += 1

        stats.documents_failed = len(stats.failures)
        stats.duration_ms = (time.perf_counter() - started) * 1000
        trace.finish(
            output={
                "status": "ok" if stats.ok else "partial_failure",
                "documents_fetched": stats.documents_fetched,
                "chunks_indexed": stats.chunks_indexed,
            }
        )
        return stats

    # -- internals ---------------------------------------------------------------

    def _resolve_adapter(self, source_type: str) -> DataSourceAdapter:
        adapter = self.adapters.get(source_type)
        if adapter is None:
            raise ValueError(
                f"No adapter registered for source_type {source_type!r}; "
                f"registered: {sorted(self.adapters)}"
            )
        if not adapter.is_available():
            raise ValueError(f"Data source {adapter.name!r} is not available right now")
        return adapter

    def _ingest_document(self, doc: RawDocument, stats: IngestionStats, *, delete: bool) -> bool:
        """Chunk -> extract -> embed -> delete-old -> store for one document.
        Raises on failure so the caller records it against this source_id.

        Delete-before-reingest runs AFTER embeddings succeed and immediately
        before the first add — failing late must not destroy the existing
        copy of a document."""
        chunks = chunk_document(doc)
        if not chunks:
            logger.warning("Document %s produced no chunks; skipping", doc.source_id)
            return False

        known = [str(doc.metadata["ticker"])] if doc.metadata.get("ticker") else []
        use_llm = self.settings.enable_llm_entity_extraction and self.engine.has_generation()
        for chunk in chunks:
            enrich_chunk(chunk, known_tickers=known)
            if use_llm:
                merged = extract_entities_llm(
                    chunk.text, self.engine, known_tickers=known
                ).flatten()
                chunk.entities = sorted(set(chunk.entities) | set(merged))

        # Embed everything first; nothing destructive has happened yet.
        cap = self.settings.pinecone_metadata_cap_bytes
        batch_size = max(self.settings.ingest_batch_size, 1)
        batches: list[tuple[list[Chunk], list[list[float]]]] = []
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            vectors = self._embed_batch([c.text for c in batch], stats)
            for chunk in batch:
                _, was_truncated = fit_metadata(to_metadata(chunk), cap)
                if was_truncated:
                    # The store persists the fitted view (see PineconeVectorStore.add);
                    # count it here so callers can watch retrieval-fidelity loss.
                    stats.chunks_truncated_for_metadata += 1
            batches.append((batch, vectors))

        if delete:
            self.store.delete_source(doc.source_id)
            stats.deleted_sources.append(doc.source_id)

        for batch, vectors in batches:
            self.store.add(batch, vectors)
            stats.chunks_indexed += len(batch)

        self._record_embedding_identity(stats)
        return True

    def _embed_batch(self, texts: list[str], stats: IngestionStats) -> list[list[float]]:
        results: list[EmbeddingResult] = self.engine.embed(texts)
        if len(results) != len(texts):
            raise RuntimeError(
                f"Embedding engine returned {len(results)} vectors for {len(texts)} inputs"
            )
        for result in results:
            if len(result.vector) != self.settings.embedding_dimension:
                raise RuntimeError(
                    f"Embedding dimension mismatch: got {len(result.vector)}, "
                    f"index expects {self.settings.embedding_dimension}"
                )
        provider = results[0].provider
        if stats.embedding_provider is None:
            stats.embedding_provider = provider
        elif stats.embedding_provider != provider:
            # A fallback mid-document means mixed-dimension risk; treat as fatal
            # for this batch rather than storing incompatible vectors.
            raise RuntimeError(
                f"Embedding provider changed mid-run: {stats.embedding_provider} -> {provider}"
            )
        return [result.vector for result in results]

    def _record_embedding_identity(self, stats: IngestionStats) -> None:
        if stats.embedding_model is not None:
            return
        for provider in self.engine.providers:
            if provider.name == stats.embedding_provider:
                model = getattr(provider, "embedding_model", None)
                if model:
                    stats.embedding_model = str(model)


def _failure_stage(exc: Exception) -> str:
    """Best-effort stage label. In this pipeline the engine is only called for
    embeddings (the LLM extraction path swallows its own errors), so any
    provider-taxonomy error means the embed stage."""
    from llm_providers.base import ProviderError

    if isinstance(exc, ProviderError):
        return "embed"
    name = type(exc).__name__
    if "Pinecone" in name or "Vector" in name or "Index" in name:
        return "store"
    return "pipeline"
