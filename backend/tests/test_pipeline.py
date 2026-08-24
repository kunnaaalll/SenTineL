"""Phase 2 ingestion-pipeline tests (offline).

Covers: happy-path statistics, provenance flow, idempotent re-ingestion,
delete-before-reingest (including stale-orphan cleanup and the no-delete
mode), per-document partial-failure isolation, Pinecone metadata-size
protection, and adapter resolution errors. All through fakes — no network.
"""

from datetime import date
from typing import cast

import pytest
from fakes import FakeAdapter, FakeVectorStore, ScriptedProvider, transient

from ingestion.pipeline import IngestionPipeline, IngestionStats
from llm_providers.base import TransientProviderError
from llm_providers.engine import LLMEngine
from models.schemas import RawDocument
from retrieval.pinecone_store import (
    DEFAULT_METADATA_CAP_BYTES,
    PINECONE_METADATA_LIMIT_BYTES,
    fit_metadata,
    metadata_size_bytes,
)


def make_document(
    source_id="SEC:AAPL:10-K:2024-11-01",
    ticker="AAPL",
    text=None,
) -> RawDocument:
    body = text or (
        "Item 7. Management's Discussion and Analysis\n\n"
        "Revenue was $391,035 million in fiscal 2024, up 5% year over year.\n\n"
        "Net income reached $93,736 with gross margin at 46.2%."
    )
    return RawDocument(
        source_id=source_id,
        source_type="sec_filing",
        title=f"{ticker} 10-K filed 2024-11-01",
        published_date=date(2024, 11, 1),
        raw_text=body,
        metadata={
            "ticker": ticker,
            "title": f"{ticker} 10-K filed 2024-11-01",
            "url": "https://www.sec.gov/archives/example.htm",
            "published_date": "2024-11-01",
        },
    )


def make_pipeline(
    documents, *, store=None, engine=None, settings=None
) -> tuple[IngestionPipeline, FakeVectorStore, LLMEngine]:
    store = store or FakeVectorStore()
    embedder = ScriptedProvider("embedder", embedding_model="text-embedding-3-small")
    engine = engine or LLMEngine(providers=[embedder])
    adapter = FakeAdapter(documents)
    from config.settings import Settings

    # ScriptedProvider yields 3-dim vectors; align the index dimension.
    resolved_settings = settings or Settings(_env_file=None, embedding_dimension=3)
    pipeline = IngestionPipeline(
        adapters={"sec_filing": adapter},
        engine=engine,
        store=store,
        settings=resolved_settings,
    )
    return pipeline, store, engine


# --------------------------------------------------------------------------
# Happy path / stats / provenance
# --------------------------------------------------------------------------


class TestHappyPath:
    def test_stats_report_documents_and_chunks(self):
        doc = make_document()
        pipeline, store, _ = make_pipeline([doc])
        stats = pipeline.ingest({"ticker": "AAPL", "filing_type": "10-K"})
        assert isinstance(stats, IngestionStats)
        assert stats.documents_fetched == 1
        assert stats.documents_ingested == 1
        assert stats.chunks_indexed == len(store.vectors)
        assert stats.ok is True
        assert stats.failures == []
        assert stats.embedding_provider == "embedder"
        assert stats.embedding_model == "text-embedding-3-small"
        assert stats.duration_ms >= 0

    def test_query_params_forwarded_to_adapter(self):
        doc = make_document()
        pipeline, _, _ = make_pipeline([doc])
        params = {"ticker": "AAPL", "filing_type": "10-K", "limit": 3}
        pipeline.ingest(params)
        # FakeAdapter records what it was asked to fetch.
        adapter = cast(FakeAdapter, pipeline.adapters["sec_filing"])
        assert adapter.fetch_calls == [params]

    def test_provenance_stored_with_every_chunk(self):
        doc = make_document(text="Short filing body that yields one chunk of prose.")
        pipeline, store, _ = make_pipeline([doc])
        pipeline.ingest({"ticker": "AAPL"})
        for _, meta in store.vectors.values():
            assert meta["source_id"] == "SEC:AAPL:10-K:2024-11-01"
            assert meta["ticker"] == "AAPL"
            assert meta["title"].endswith("filed 2024-11-01")
            assert meta["url"].startswith("https://www.sec.gov/")
            assert meta["date"] == "2024-11-01"

    def test_entities_extracted_deterministically(self):
        doc = make_document(text="Revenue was $391,035, up 5% in fiscal 2024.")
        pipeline, store, _ = make_pipeline([doc])
        pipeline.ingest({"ticker": "AAPL"})
        stored_entities = [meta.get("entities") or [] for _, meta in store.vectors.values()]
        flattened = [entity for batch in stored_entities for entity in batch]
        assert "$391,035" in flattened
        assert "5%" in flattened
        assert "FY2024" in flattened
        assert "AAPL" in flattened


# --------------------------------------------------------------------------
# Idempotency + delete-before-reingest
# --------------------------------------------------------------------------


class TestIdempotency:
    def test_reingest_same_document_is_idempotent(self):
        doc = make_document()
        pipeline, store, _ = make_pipeline([doc])
        first = pipeline.ingest({"ticker": "AAPL"})
        count_after_first = len(store.vectors)
        second = pipeline.ingest({"ticker": "AAPL"})
        assert second.chunks_indexed == first.chunks_indexed
        assert len(store.vectors) == count_after_first  # no duplication
        # One delete per ingest run: unconditional delete-before-reingest also
        # cleans orphans left by a crashed first-ever ingestion.
        assert store.delete_calls == ["SEC:AAPL:10-K:2024-11-01", "SEC:AAPL:10-K:2024-11-01"]

    def test_delete_before_reingest_removes_stale_orphans(self):
        doc = make_document()
        pipeline, store, _ = make_pipeline([doc])
        # Simulate an earlier run whose chunk boundaries produced a now-stale id.
        store.seed_stale_vector("deadbeef0000000f", "SEC:AAPL:10-K:2024-11-01")
        pipeline.ingest({"ticker": "AAPL"})
        assert "deadbeef0000000f" not in store.vectors  # orphan wiped
        assert all(
            meta["source_id"] == "SEC:AAPL:10-K:2024-11-01" for _, meta in store.vectors.values()
        )

    def test_delete_opt_out_preserves_old_vectors(self):
        doc = make_document()
        pipeline, store, _ = make_pipeline([doc])
        store.seed_stale_vector("staleoldvectors", "SEC:AAPL:10-K:2024-11-01")
        pipeline.ingest({"ticker": "AAPL"}, delete_existing=False)
        assert "staleoldvectors" in store.vectors
        assert store.delete_calls == []

    def test_failed_reingest_keeps_existing_vectors(self):
        """Embedding failure on re-run must not destroy the already-indexed copy."""
        doc = make_document()
        pipeline, store, _ = make_pipeline([doc])
        pipeline.ingest({"ticker": "AAPL"})
        before = dict(store.vectors)

        # Now break embeddings and retry (no real sleeps — injected sleeper).
        broken = ScriptedProvider("broken", embed_script=[transient("down")])
        engine = LLMEngine(providers=[broken], sleeper=lambda _s: None, jitter=lambda _a: 0.0)
        pipeline.engine = engine
        stats = pipeline.ingest({"ticker": "AAPL"}, delete_existing=True)

        assert stats.documents_ingested == 0
        assert stats.failures[0].stage == "embed"
        assert store.vectors == before  # untouched — deletion never happened


# --------------------------------------------------------------------------
# Partial failures
# --------------------------------------------------------------------------


class TestPartialFailures:
    def test_second_document_failure_does_not_block_first(self, monkeypatch):
        good = make_document(source_id="SEC:AAPL:10-K:2024-11-01")
        bad = make_document(source_id="SEC:BAD:10-K:2024-01-01", ticker="BAD")
        pipeline, store, _ = make_pipeline([good, bad])

        original_embed = pipeline._embed_batch
        calls = {"n": 0}

        def fail_second_document(texts, stats_arg):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise TransientProviderError("second document always fails")
            return original_embed(texts, stats_arg)

        monkeypatch.setattr(pipeline, "_embed_batch", fail_second_document)
        stats = pipeline.ingest({"ticker": "*"})

        assert stats.documents_fetched == 2
        assert stats.documents_ingested == 1
        assert stats.documents_failed == 1
        assert stats.failures[0].source_id == "SEC:BAD:10-K:2024-01-01"
        assert stats.failures[0].stage == "embed"
        assert len(store.vectors) > 0  # first document survived

    def test_fetch_failure_recorded_not_raised(self):
        class ExplodingAdapter(FakeAdapter):
            def fetch(self, query_params):
                raise ConnectionError("network down")

        from config.settings import Settings

        pipeline = IngestionPipeline(
            adapters={"sec_filing": ExplodingAdapter([])},
            engine=LLMEngine(providers=[ScriptedProvider("e")]),
            store=FakeVectorStore(),
            settings=Settings(_env_file=None),
        )
        stats = pipeline.ingest({"ticker": "AAPL"})
        assert stats.documents_fetched == 0
        assert stats.documents_failed == 1
        assert stats.failures[0].stage == "fetch"


# --------------------------------------------------------------------------
# Adapter resolution
# --------------------------------------------------------------------------


class TestAdapterResolution:
    def test_unknown_source_type_raises_value_error(self):
        pipeline, _, _ = make_pipeline([])
        with pytest.raises(ValueError):
            pipeline.ingest({}, source_type="news")

    def test_unavailable_adapter_raises_value_error(self):
        adapter = FakeAdapter([], available=False)
        from config.settings import Settings

        pipeline = IngestionPipeline(
            adapters={"sec_filing": adapter},
            engine=LLMEngine(providers=[ScriptedProvider("e")]),
            store=FakeVectorStore(),
            settings=Settings(_env_file=None),
        )
        with pytest.raises(ValueError):
            pipeline.ingest({})


# --------------------------------------------------------------------------
# Metadata size guard (audit risk #3)
# --------------------------------------------------------------------------


class TestMetadataCap:
    def _oversized_doc(self) -> RawDocument:
        giant_table = "\n".join(f"| row {i} | $1,23{i % 10} | note {i} |" for i in range(1200))
        return make_document(text=giant_table)  # tables stay atomic -> huge single chunk

    def test_fit_metadata_small_passthrough_untouched(self):
        meta = {"text": "tiny", "source_id": "s"}
        fitted, truncated = fit_metadata(meta, max_bytes=DEFAULT_METADATA_CAP_BYTES)
        assert truncated is False
        assert fitted is meta  # same object returned, not a copy

    def test_fit_metadata_truncates_giant_text_under_cap(self):
        meta = {"text": "x" * 100_000, "source_id": "s", "title": "T"}
        fitted, truncated = fit_metadata(meta, max_bytes=DEFAULT_METADATA_CAP_BYTES)
        assert truncated is True
        assert metadata_size_bytes(fitted) <= DEFAULT_METADATA_CAP_BYTES
        assert fitted["text"].endswith("[truncated]")
        assert fitted["source_id"] == "s" and fitted["title"] == "T"

    def test_fit_metadata_drops_footnotes_before_text(self):
        meta = {
            "text": "y" * 45_000,
            "footnotes": ["(1) " + "note " * 200],
            "source_id": "s",
        }
        fitted, truncated = fit_metadata(meta, max_bytes=DEFAULT_METADATA_CAP_BYTES)
        assert truncated is True
        assert "footnotes" not in fitted
        assert fitted["text"].startswith("yyy")

    def test_fit_metadata_never_mutates_original(self):
        original_text = "z" * 50_000
        meta = {"text": original_text}
        fit_metadata(meta, max_bytes=5_000)
        assert len(meta["text"]) == 50_000  # caller's copy intact

    def test_pipeline_counts_truncated_chunks_and_store_stays_under_cap(self):
        doc = self._oversized_doc()
        pipeline, store, _ = make_pipeline([doc])
        stats = pipeline.ingest({"ticker": "AAPL"})
        assert stats.ok is True
        assert stats.chunks_truncated_for_metadata >= 1
        for _, meta in store.vectors.values():
            assert metadata_size_bytes(meta) <= PINECONE_METADATA_LIMIT_BYTES

    def test_default_cap_sits_below_pinecone_limit(self):
        assert DEFAULT_METADATA_CAP_BYTES < PINECONE_METADATA_LIMIT_BYTES


# --------------------------------------------------------------------------
# Stats shape safety
# --------------------------------------------------------------------------


def test_stats_defaults_are_safe():
    stats = IngestionStats()
    assert stats.ok is True  # nothing failed yet
    assert stats.failures == []
    assert stats.documents_fetched == 0
