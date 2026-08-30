"""Phase 2 chains tests (offline): query rewriting, RAG citation mapping,
grounding, insufficient-evidence refusal, and trace wiring."""

import pytest
from fakes import FakeVectorStore, RecordingTracer, ScriptedProvider

from chains.query_rewrite import QueryRewriter, RewriteResult
from chains.rag_chain import RagChain, build_context, parse_citations
from llm_providers.base import ProviderUnavailableError
from llm_providers.engine import LLMEngine
from models.schemas import RetrievedChunk


def make_engine_with_reply(reply: str) -> LLMEngine:
    provider = ScriptedProvider("gen", generation_script=[reply])
    return LLMEngine(providers=[provider])


# --------------------------------------------------------------------------
# Query rewrite: deterministic
# --------------------------------------------------------------------------


class TestDeterministicRewrite:
    def test_whitespace_and_filler_stripped(self):
        rewriter = QueryRewriter(known_tickers={"AAPL"})
        result = rewriter.rewrite("  Can you please tell me   what drove AAPL revenue?  ")
        assert result.rewritten == "what drove AAPL revenue?"
        assert result.changed is True
        assert result.mode == "heuristic"

    def test_cashtag_normalized_and_filter_emitted(self):
        rewriter = QueryRewriter()
        result = rewriter.rewrite("What drove $msft revenue growth?")
        assert "$MSFT" in result.rewritten
        assert result.tickers == ["MSFT"]
        assert result.filters == {"ticker": "MSFT"}

    def test_two_tickers_emit_no_filter_comparison_safety(self):
        rewriter = QueryRewriter()
        result = rewriter.rewrite("Compare AAPL and MSFT gross margins")
        assert set(result.tickers) == {"AAPL", "MSFT"}
        assert result.filters == {}

    def test_no_ticker_no_filter(self):
        rewriter = QueryRewriter(known_tickers=set())
        result = rewriter.rewrite("Which sectors led the market rally?")
        assert result.tickers == []
        assert result.filters == {}
        assert result.changed is False

    def test_rewrite_never_returns_empty(self):
        rewriter = QueryRewriter(known_tickers=set())
        result = rewriter.rewrite("hey sentinel,")
        assert result.rewritten.strip() != ""

    def test_llm_mode_uses_engine_when_enabled(self, clean_settings):
        engine = make_engine_with_reply('{"query": "AAPL fiscal 2024 revenue drivers"}')
        settings = clean_settings(enable_llm_query_rewrite=True)
        rewriter = QueryRewriter(settings=settings, engine=engine)
        result = rewriter.rewrite("can you tell me what drove aapl's revenue?")
        assert result.mode == "llm"
        assert result.rewritten == "AAPL fiscal 2024 revenue drivers"

    def test_llm_failure_falls_back_to_heuristic(self, clean_settings):
        engine = make_engine_with_reply("this is not json")
        settings = clean_settings(enable_llm_query_rewrite=True)
        rewriter = QueryRewriter(settings=settings, engine=engine)
        result = rewriter.rewrite("Compare AAPL and MSFT gross margins")
        assert result.mode == "heuristic"
        assert result.rewritten == "Compare AAPL and MSFT gross margins"


# --------------------------------------------------------------------------
# Context building / citation parsing
# --------------------------------------------------------------------------


class TestBuildContextAndCitations:
    def _chunk(self, cid, text, score=0.9) -> RetrievedChunk:
        return RetrievedChunk(
            chunk_id=cid,
            source_id=f"SEC:AAPL:10-K:{cid}",
            source_type="sec_filing",
            section="Item 7",
            text=text,
            metadata={"title": f"AAPL filing {cid}", "url": f"https://sec.gov/{cid}"},
            score=score,
        )

    def test_build_context_numbers_excerpts(self):
        chunks = [self._chunk("a", "alpha text"), self._chunk("b", "beta text")]
        context = build_context(chunks, excerpt_chars=100, budget_chars=10_000)
        assert "[1] (" in context and "[2] (" in context
        assert "alpha text" in context and "beta text" in context

    def test_build_context_respects_budget(self):
        chunks = [self._chunk(str(i), "word " * 50) for i in range(1, 8)]
        context = build_context(chunks, excerpt_chars=300, budget_chars=400)
        assert len(context) <= 500

    def test_parse_citations_validates_range_and_dedupe(self):
        text = "Revenue rose [1]. Margins [2][2] held. Phantom [99] ignored [0] too."
        assert parse_citations(text, max_index=3) == [1, 2]

    def test_parse_citations_none(self):
        assert parse_citations("No markers at all.", max_index=5) == []


# --------------------------------------------------------------------------
# RagChain end to end with fakes
# --------------------------------------------------------------------------


def seeded_store() -> FakeVectorStore:
    store = FakeVectorStore()
    from models.schemas import Chunk

    store.add(
        [
            Chunk(
                chunk_id="aaa",
                source_id="SEC:AAPL:10-K:2024-11-01",
                source_type="sec_filing",
                section="Item 7 - MD&A",
                page_or_position="chars 0-800",
                text="Fiscal 2024 total net sales were $391,035 million.",
                entities=["AAPL", "FY2024", "$391,035"],
                metadata={
                    "ticker": "AAPL",
                    "title": "Apple Inc. 10-K filed 2024-11-01",
                    "url": "https://www.sec.gov/archives/aapl.htm",
                },
            ),
            Chunk(
                chunk_id="bbb",
                source_id="SEC:MSFT:10-K:2024-07-30",
                source_type="sec_filing",
                section="Item 7 - MD&A",
                page_or_position="chars 0-800",
                text="Microsoft revenue grew 16% to $245,122 million in fiscal 2024.",
                entities=["MSFT"],
                metadata={"ticker": "MSFT", "title": "Microsoft 10-K filed 2024-07-30"},
            ),
        ],
        [[3.0, 1.0], [2.6, 1.4]],
    )
    return store


ANSWER_WITH_CITATIONS = (
    "Apple's fiscal 2024 total net sales were $391,035 million [1]. "
    "Microsoft grew revenue 16% to $245,122 million [2]."
)


def make_rag(reply=ANSWER_WITH_CITATIONS, *, tracer=None, rewriter=None, clean_settings=None):
    provider = ScriptedProvider("embedder")  # embeds + generates
    provider.generation_script = [reply]
    engine = LLMEngine(providers=[provider])
    settings = clean_settings(rag_top_k=2, embedding_dimension=3) if clean_settings else None
    chain = RagChain(
        engine,
        seeded_store(),
        settings=settings,
        tracer=tracer or RecordingTracer(),
        rewriter=rewriter,
    )
    return chain


class TestRagHappyPath:
    def test_answer_citations_agent_path_trace_url(self, clean_settings):
        tracer = RecordingTracer(url="lf://trace/xyz")
        chain = make_rag(clean_settings=clean_settings, tracer=tracer)
        result = chain.run("What was Apple's fiscal 2024 revenue?")
        assert result.insufficient_evidence is False
        assert "$391,035" in result.answer
        assert result.agent_path == ["embed", "retrieve", "generate"]
        assert result.trace_url == "lf://trace/xyz"
        assert len(result.citations) == 2
        first = result.citations[0]
        assert first["source_id"] == "SEC:AAPL:10-K:2024-11-01"
        assert first["chunk_id"] == "aaa"
        assert first["title"].endswith("filed 2024-11-01")
        assert first["excerpt"].startswith("Fiscal 2024 total net sales")
        assert first["score"] > 0.9
        assert first["url"] == "https://www.sec.gov/archives/aapl.htm"

    def test_citations_only_from_retrieved_chunks(self, clean_settings):
        # Model cites [1] twice and phantom [7].
        reply = "Sales were huge [1] and also [7] per [1]."
        chain = make_rag(reply=reply, clean_settings=clean_settings)
        result = chain.run("revenue?")
        assert [c["chunk_id"] for c in result.citations] == ["aaa"]

    def test_rewriter_step_recorded_in_agent_path_and_filters_merged(self, clean_settings):
        class TickerRewriter:
            def rewrite(self, question):
                return RewriteResult(
                    original=question,
                    rewritten=question,
                    tickers=["MSFT"],
                    filters={"ticker": "MSFT"},
                    mode="heuristic",
                    changed=False,
                )

        chain = make_rag(rewriter=TickerRewriter(), clean_settings=clean_settings)
        result = chain.run("what about microsoft?")
        assert result.agent_path[0] == "rewrite"
        assert result.filters_used == {"ticker": "MSFT"}
        # Only MSFT chunk survived filtering.
        assert all(c["source_id"].startswith("SEC:MSFT") for c in result.citations)

    def test_caller_filters_override_rewriter_filters(self, clean_settings):
        class TickerRewriter:
            def rewrite(self, question):
                return RewriteResult(
                    original=question,
                    rewritten=question,
                    tickers=["MSFT"],
                    filters={"ticker": "MSFT"},
                )

        chain = make_rag(rewriter=TickerRewriter(), clean_settings=clean_settings)
        result = chain.run("q", filters={"ticker": "AAPL"})
        assert result.filters_used == {"ticker": "AAPL"}
        assert all(c["source_id"].startswith("SEC:AAPL") for c in result.citations)

    def test_auto_ingest_when_no_chunks_initially(self, clean_settings):
        class FakePipeline:
            def __init__(self, store):
                self.store = store
                self.ingested_calls = []

            def ingest(self, params, source_type="sec_filing"):
                self.ingested_calls.append((params, source_type))
                from models.schemas import Chunk
                self.store.add(
                    [
                        Chunk(
                            chunk_id="nvda1",
                            source_id="SEC:NVDA:10-K:2024-02-21",
                            source_type="sec_filing",
                            section="Item 7 - MD&A",
                            page_or_position="chars 0-800",
                            text="Data Center revenue for fiscal 2024 was $47.5 billion, up 217%.",
                            entities=["NVDA", "FY2024"],
                            metadata={"ticker": "NVDA", "title": "NVIDIA 10-K"},
                        )
                    ],
                    [[2.5, 1.5]],
                )

        empty_store = FakeVectorStore()
        provider = ScriptedProvider("embedder")
        provider.generation_script = ["NVIDIA data center revenue was $47.5B [1]."]
        engine = LLMEngine(providers=[provider])
        settings = clean_settings(rag_top_k=2, embedding_dimension=3)
        pipeline = FakePipeline(empty_store)

        rewriter = QueryRewriter(settings=settings)
        chain = RagChain(
            engine,
            empty_store,
            settings=settings,
            tracer=RecordingTracer(),
            rewriter=rewriter,
            pipeline=pipeline,
        )
        result = chain.run("What was NVIDIA data center revenue?")
        assert result.insufficient_evidence is False
        assert "auto_ingest" in result.agent_path
        assert len(pipeline.ingested_calls) == 1
        assert pipeline.ingested_calls[0][0]["ticker"] == "NVDA"
        assert len(result.citations) == 1
        assert result.citations[0]["source_id"] == "SEC:NVDA:10-K:2024-02-21"


class TestInsufficientEvidence:
    def test_zero_retrieved_chunks_skips_generation(self, clean_settings):
        empty_store = FakeVectorStore()
        provider = ScriptedProvider("p")
        engine = LLMEngine(providers=[provider])
        settings = clean_settings(embedding_dimension=3)
        chain = RagChain(engine, empty_store, settings=settings, tracer=RecordingTracer())
        result = chain.run("What was revenue?")
        assert result.insufficient_evidence is True
        assert result.citations == []
        assert "generate" not in result.agent_path
        assert provider.generate_calls == 0  # never asked the model to improvise

    def test_model_marker_translated_to_refusal(self, clean_settings):
        reply = "INSUFFICIENT_EVIDENCE — no excerpts mention backlog figures."
        chain = make_rag(reply=reply, clean_settings=clean_settings)
        result = chain.run("What was Apple's backlog?")
        assert result.insufficient_evidence is True
        assert result.citations == []
        assert result.answer.startswith("I don't have enough evidence")
        assert "backlog" in result.answer.lower()  # model's note preserved
        assert "INSUFFICIENT_EVIDENCE" not in result.answer

    def test_generation_unavailable_propagates_after_retrieval(self, clean_settings):
        class EmbedOnlyEngine(LLMEngine):
            """Embedding works; every generate raises provider-unavailable."""

            def generate(self, *args, **kwargs):
                raise ProviderUnavailableError("All configured LLM providers failed for generate")

        embedder = ScriptedProvider("embedder")
        engine = EmbedOnlyEngine(providers=[embedder])
        settings = clean_settings(embedding_dimension=3)
        chain = RagChain(engine, seeded_store(), settings=settings, tracer=RecordingTracer())
        with pytest.raises(ProviderUnavailableError):
            chain.run("What was revenue?")
        assert embedder.embed_calls == 1  # retrieval ran before the failure


# --------------------------------------------------------------------------
# Trace integration
# --------------------------------------------------------------------------


def test_rag_chain_records_spans_through_tracer(clean_settings):
    tracer = RecordingTracer(url=None)
    chain = make_rag(clean_settings=clean_settings, tracer=tracer)
    result = chain.run("revenue?")
    names = [record.name for record in tracer.records]
    assert names == ["embed", "retrieve", "generate"]
    assert result.trace_url is None  # NullTracer-style URL
