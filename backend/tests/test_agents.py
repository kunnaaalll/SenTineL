"""Phase 3 agent team tests (offline).

Every node is exercised in isolation with scripted providers, the in-memory
vector store, canned adapters, and recording tracers — plus full-graph
integration through the compiled LangGraph runner. No network anywhere.
"""

import json
from typing import cast

import pytest
from fakes import (
    FakeAdapter,
    FakeVectorStore,
    RecordingTracer,
    ScriptedProvider,
)
from pydantic import ValidationError

from agents.compare_agent import (
    CompareAgent,
    build_comparison_table,
    comparison_warranted,
)
from agents.extract_agent import ExtractAgent, parse_numeric, unit_for
from agents.fetch_agent import FetchAgent, QueryPlanner
from agents.graph import (
    SentinelQueryService,
    _guarded,
    classify_query,
    route_after_extract,
)
from agents.state import ExtractedFact, initial_state, unique_chunks
from agents.synthesize_agent import REFUSAL_PREFIX, SynthesizeAgent
from chains.rag_chain import INSUFFICIENT_MARKER, RagAnswer, RagChain
from config.settings import Settings
from ingestion.pipeline import IngestionStats
from llm_providers.base import (
    InvalidRequestError,
    ProviderUnavailableError,
    TransientProviderError,
)
from llm_providers.engine import LLMEngine
from models.schemas import RetrievedChunk

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def make_chunk(
    cid,
    text,
    *,
    source_type="sec_filing",
    ticker="AAPL",
    score=0.9,
    source_id=None,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid,
        source_id=source_id or f"SEC:{ticker}:10-K:2024-11-01",
        source_type=source_type,
        section="Item 7 - MD&A",
        page_or_position="24",
        text=text,
        entities=[ticker],
        metadata={
            "ticker": ticker,
            "title": f"{ticker} annual report",
            "url": f"https://www.sec.example/{ticker}",
            "published_date": "2024-11-01",
            "date": "2024-11-01",
        },
        score=score,
    )


def seed_store(*chunks) -> FakeVectorStore:
    store = FakeVectorStore()
    if chunks:
        store.add(list(chunks), [[float(len(c.text)), 1.0, 0.5] for c in chunks])
    return store


def script_engine(*outputs) -> LLMEngine:
    return LLMEngine(providers=[ScriptedProvider("script", generation_script=list(outputs))])


FACTS_JSON = json.dumps(
    {
        "facts": [
            {
                "entity": "AAPL",
                "metric": "Total Net Sales",
                "value": "$391,035 million",
                "period": "FY2024",
                "kind": "reported",
                "confidence": 0.95,
            }
        ]
    }
)

SYNTH_TEXT = "Apple's total net sales were $391,035 million in FY2024 [1]."


class RecordingPipeline:
    """Stands in for IngestionPipeline; adds a chunk to the store on ingest."""

    def __init__(
        self, store: FakeVectorStore, chunk_text="Live-ingested AAPL revenue $400,000 million"
    ):
        self.store = store
        self.calls: list[tuple[dict, str]] = []
        self.chunk_text = chunk_text

    def ingest(self, query_params, *, source_type="sec_filing"):
        self.calls.append((dict(query_params), source_type))
        chunk = make_chunk(
            f"LIVE:{source_type}:{len(self.calls)}",
            self.chunk_text,
            source_type=source_type,
            ticker=query_params.get("ticker", "AAPL"),
            source_id=f"LIVE:{source_type}:{query_params.get('ticker')}",
        )
        self.store.add([chunk], [[10.0, 1.0, 0.5]])
        stats = IngestionStats(documents_fetched=1, chunks_indexed=1)
        return stats


# --------------------------------------------------------------------------
# State contract
# --------------------------------------------------------------------------


class TestAgentState:
    def test_initial_state_has_every_key_nodes_read(self):
        state = initial_state("Compare AAPL and MSFT revenue", force_agents=True)
        expected_keys = {
            "query",
            "query_type",
            "retrieved_chunks",
            "extracted_facts",
            "comparison_table",
            "final_answer",
            "citations",
            "trace_id",
            "agent_path",
            "force_agents",
            "tickers",
            "unavailable_sources",
            "node_errors",
            "ingested_keys",
            "limitations",
            "trace_urls",
        }
        assert expected_keys == set(state.keys())
        assert state["force_agents"] is True
        assert state["agent_path"] == ["classify"]
        assert state["retrieved_chunks"] == []

    def test_fact_round_trip_through_dict(self):
        fact = ExtractedFact(
            entity="AAPL", metric="revenue", value="$391,035", source_chunk_id="c1"
        )
        restored = ExtractedFact(**fact.model_dump())
        assert restored == fact

    def test_fact_rejects_unknown_fields(self):
        with pytest.raises(ValidationError):
            ExtractedFact(**{"entity": "X", "source_chunk_id": "c", "bogus": "nope"})

    def test_unique_chunks_keeps_best_score_and_dedups(self):
        low = make_chunk("c1", "text one", score=0.2)
        high = make_chunk("c1", "text one", score=0.8)
        other = make_chunk("c2", "text two", score=0.5)
        merged = unique_chunks([low, high, other])
        assert [c.chunk_id for c in merged] == ["c1", "c2"]
        assert merged[0].score == 0.8


# --------------------------------------------------------------------------
# Deterministic routing
# --------------------------------------------------------------------------


class TestClassifyQuery:
    @pytest.mark.parametrize(
        "question",
        [
            "What was Apple's total net sales?",
            "Summarize Apple FY2024 revenue",  # single period token set
            "What does the filing say about risk factors?",
        ],
    )
    def test_simple_questions(self, question):
        assert classify_query(question) == "simple"

    @pytest.mark.parametrize(
        "question",
        [
            "Compare AAPL and MSFT revenue",
            "Apple versus Microsoft margins",
            "How did revenue change from 2023 to 2024?",  # two years
            "Compare debt load across competitors vs peers FY2023 Q1 FY2024",
        ],
    )
    def test_multi_hop_questions(self, question):
        assert classify_query(question) == "multi_hop"

    def test_route_after_extract_skips_compare_single_entity(self):
        facts = [ExtractedFact(entity="AAPL", value="$1", source_chunk_id="c")]
        assert comparison_warranted(facts) is False
        assert route_after_extract({"extracted_facts": [facts[0].model_dump()]}) == "synthesize"

    def test_route_after_extract_selects_compare(self):
        facts = [
            ExtractedFact(entity="AAPL", value="$1", source_chunk_id="c"),
            ExtractedFact(entity="MSFT", value="$2", source_chunk_id="c"),
        ]
        assert (
            route_after_extract({"extracted_facts": [f.model_dump() for f in facts]}) == "compare"
        )


# --------------------------------------------------------------------------
# Planner
# --------------------------------------------------------------------------


class TestQueryPlanner:
    def test_plan_detects_tickers_years_and_news_hint(self):
        plan = QueryPlanner().plan("Compare AAPL and $MSFT news about 2024 guidance")
        assert sorted(plan.tickers) == ["AAPL", "MSFT"]
        assert plan.date_range == ("2024-01-01", "2024-12-31")
        assert "news" in plan.source_types and "sec_filing" in plan.source_types

    def test_plan_defaults_to_filings_only(self):
        plan = QueryPlanner().plan("What was AAPL's revenue?")
        assert plan.tickers == ["AAPL"]
        assert plan.source_types == ["sec_filing"]
        assert plan.date_range is None


# --------------------------------------------------------------------------
# Fetch agent
# --------------------------------------------------------------------------


def build_fetch(store, *, sec_available=True, news_available=False, pipeline=None, adapters=None):
    engine = script_engine()
    resolved_adapters = adapters or {
        "sec_filing": FakeAdapter([], name="sec_edgar", available=sec_available),
        "news": FakeAdapter([], name="news_api", available=news_available),
    }
    return FetchAgent(
        engine=engine,
        store=store,
        adapters=resolved_adapters,
        pipeline=pipeline,
        settings=Settings(_env_file=None),
    )


class TestFetchAgent:
    def test_indexed_chunks_found_without_live_ingestion(self):
        chunk = make_chunk("s1", "Apple total net sales $391,035 million FY2024")
        agent = build_fetch(seed_store(chunk))
        updates = agent(initial_state("What was Apple's total net sales?"))
        assert [c.chunk_id for c in updates["retrieved_chunks"]] == ["s1"]
        assert updates["ingested_keys"] == []
        assert updates["agent_path"][-1] == "fetch"

    def test_unavailable_source_reported_explicitly(self):
        agent = build_fetch(FakeVectorStore(), news_available=False)
        updates = agent(initial_state("AAPL latest news about guidance"))
        assert updates["retrieved_chunks"] == []
        assert any("news_api" in note for note in updates["unavailable_sources"])

    def test_live_ingestion_triggered_once_then_loop_protected(self):
        chunk = make_chunk("s1", "Apple revenue context")
        store = seed_store(chunk)
        pipeline = RecordingPipeline(store)
        agent = build_fetch(
            store,
            pipeline=pipeline,
            news_available=True,
        )
        # News-hint query with no indexed news -> live ingest fires once.
        first = agent(initial_state("AAPL news about revenue"))
        calls_after_first = len(pipeline.calls)
        assert calls_after_first >= 1
        ingested_after_first = len(first["ingested_keys"])
        assert ingested_after_first >= 1

        # Re-running with the accumulated state must not re-ingest.
        second_state = {
            **initial_state("AAPL news about revenue"),
            "ingested_keys": first["ingested_keys"],
        }
        second = agent(second_state)
        assert len(pipeline.calls) == calls_after_first  # loop protection held
        assert len(second["ingested_keys"]) == ingested_after_first

    def test_live_ingestion_budget_bounded(self):
        store = FakeVectorStore()
        pipeline = RecordingPipeline(store)
        engine = script_engine()
        adapters = {
            "sec_filing": FakeAdapter([], name="sec_edgar", available=True),
            "news": FakeAdapter([], name="news_api", available=True),
        }
        agent = FetchAgent(
            engine=engine,
            store=store,
            adapters=adapters,
            pipeline=pipeline,
            settings=Settings(_env_file=None),
            max_live_ingests=2,
        )
        updates = agent(cast("dict", initial_state("AAPL and MSFT news and filings overview")))
        assert len(pipeline.calls) <= 2
        assert len(updates["limitations"]) >= 1  # skipped combos are reported

    def test_duplicate_chunks_collapse_in_merge(self):
        chunk = make_chunk("s1", "Apple revenue $391,035 million")

        class DupStore(FakeVectorStore):
            def search(self, query_vector, top_k=5, filters=None):
                found = super().search(query_vector, top_k=top_k, filters=filters)
                return ([found[0], found[0]] + found[1:])[:top_k] if found else []

        store = DupStore()
        store.add([chunk], [[10.0, 1.0, 0.5]])
        agent = build_fetch(store)
        updates = agent(cast("dict", initial_state("Apple revenue")))
        ids = [c.chunk_id for c in updates["retrieved_chunks"]]
        assert ids.count("s1") == 1

    def test_sec_and_news_combined(self):
        sec = make_chunk("sec1", "Filing revenue discussion", source_type="sec_filing")
        news = make_chunk(
            "news1",
            "Apple announced record quarterly revenue",
            source_type="news",
            source_id="NEWS:FMP:AAPL:abc",
        )
        agent = build_fetch(seed_store(sec, news), news_available=True)
        updates = agent(initial_state("AAPL revenue news"))
        ids = {c.chunk_id for c in updates["retrieved_chunks"]}
        assert {"sec1", "news1"} <= ids
        scores = [c.score for c in updates["retrieved_chunks"]]
        assert scores == sorted(scores, reverse=True)


# --------------------------------------------------------------------------
# Extract agent
# --------------------------------------------------------------------------


class TestExtractAgent:
    def test_structured_output_validated(self):
        chunk = make_chunk("c1", "Total net sales were $391,035 million in FY2024.")
        agent = ExtractAgent(engine=script_engine(FACTS_JSON))
        updates = agent({"retrieved_chunks": [chunk], "agent_path": ["fetch"]})
        facts = updates["extracted_facts"]
        assert len(facts) == 1
        fact = facts[0]
        assert fact["entity"] == "AAPL"
        assert fact["metric"] == "total net sales"
        assert fact["value"] == "$391,035 million"  # verbatim
        assert fact["numeric_value"] == pytest.approx(391_035_000_000.0)
        assert fact["unit"] == "million USD"
        assert fact["period"] == "FY2024"
        assert fact["kind"] == "reported"
        assert fact["source_chunk_id"] == "c1"  # real provenance
        assert updates["agent_path"][-1] == "extract"

    def test_model_supplied_provenance_is_overridden(self):
        payload = json.dumps(
            {
                "facts": [
                    {
                        "entity": "AAPL",
                        "value": "$1",
                        "kind": "reported",
                        "source_chunk_id": "HALLUCINATED",
                    }
                ]
            }
        )
        chunk = make_chunk("real-chunk", "Revenue $1")
        updates = ExtractAgent(engine=script_engine(payload))({"retrieved_chunks": [chunk]})
        assert updates["extracted_facts"][0]["source_chunk_id"] == "real-chunk"

    def test_malformed_json_records_error_and_falls_back(self):
        chunk = make_chunk("c1", "net income $93,736 million; revenue $391,035 million")
        agent = ExtractAgent(engine=script_engine("this is not JSON"))
        updates = agent({"retrieved_chunks": [chunk]})
        assert updates["extracted_facts"], "deterministic floor must produce facts"
        floor = updates["extracted_facts"][0]
        assert floor["confidence"] <= 0.35
        assert any("malformed" in e.get("error", "") for e in updates["node_errors"])

    def test_wrong_shape_rejected(self):
        chunk = make_chunk("c1", "revenue $1")
        bad = json.dumps({"facts": {"not": "a list"}})
        updates = ExtractAgent(engine=script_engine(bad))({"retrieved_chunks": [chunk]})
        assert updates["extracted_facts"]  # regex fallback
        assert updates["node_errors"]

    def test_provider_error_isolated_per_chunk_with_floor(self):
        chunks = [
            make_chunk("c1", "revenue $391,035 million"),
            make_chunk("c2", "cash $30 billion"),
        ]
        engine = LLMEngine(
            providers=[ScriptedProvider("dead", generation_script=[TransientProviderError("t")])]
        )
        updates = ExtractAgent(engine=engine)({"retrieved_chunks": chunks})
        kinds = {(f["entity"], f["metric"]) for f in updates["extracted_facts"]}
        assert kinds  # floor produced comparable entries
        assert all(f["kind"] == "qualitative" for f in updates["extracted_facts"])
        assert len(updates["node_errors"]) == 2

    def test_invalid_kind_and_confidence_coerced(self):
        payload = json.dumps(
            {"facts": [{"entity": "AAPL", "value": "$5", "kind": "made-up-kind", "confidence": 9}]}
        )
        chunk = make_chunk("c1", "$5")
        updates = ExtractAgent(engine=script_engine(payload))({"retrieved_chunks": [chunk]})
        fact = updates["extracted_facts"][0]
        assert fact["kind"] == "qualitative"
        assert fact["confidence"] == 1.0

    def test_identical_duplicate_facts_dedupe(self):
        payload = json.dumps(
            {
                "facts": [
                    {"entity": "AAPL", "value": "$5", "period": "FY2024"},
                    {"entity": "AAPL", "value": "$5", "period": "FY2024"},
                ]
            }
        )
        chunk = make_chunk("c1", "$5 FY2024")
        updates = ExtractAgent(engine=script_engine(payload))({"retrieved_chunks": [chunk]})
        assert len(updates["extracted_facts"]) == 1

    def test_empty_evidence_yields_no_facts_cleanly(self):
        updates = ExtractAgent(engine=script_engine(FACTS_JSON))({"retrieved_chunks": []})
        assert updates["extracted_facts"] == []

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("$391,035 million", 391_035_000_000.0),
            ("$1,234", 1234.0),
            ("12.5%", 12.5),
            ("($500)", -500.0),
            ("$2B", 2_000_000_000.0),
            ("$30 billion", 30_000_000_000.0),
            ("42 widgets", None),  # unknown suffix refused
            ("n/a", None),
            (None, None),
        ],
    )
    def test_parse_numeric(self, raw, expected):
        result = parse_numeric(raw)
        if expected is None:
            assert result is None
        else:
            assert result == pytest.approx(expected)

    def test_unit_detection(self):
        assert unit_for("$100") == "USD"
        assert unit_for("12.5%") == "%"
        assert unit_for("$2 billion") == "billion USD"
        assert unit_for("plain") is None


# --------------------------------------------------------------------------
# Compare agent
# --------------------------------------------------------------------------


def fact(entity, metric, value, period=None, unit=None, chunk="c1", **kwargs):
    return ExtractedFact(
        entity=entity,
        metric=metric,
        value=value,
        period=period,
        unit=unit,
        source_chunk_id=chunk,
        numeric_value=parse_numeric(value),
        **kwargs,
    )


class TestCompareAgent:
    def test_alignment_across_entities_and_periods(self):
        table = build_comparison_table(
            [
                fact("AAPL", "revenue", "$391,035 million", "FY2024", "million USD"),
                fact("MSFT", "revenue", "$245,122 million", "FY2024", "million USD"),
                fact("AAPL", "revenue", "$383,285 million", "FY2023", "million USD"),
            ]
        )
        assert table["warranted"] is True
        metrics = {(row["metric"], row["period"]) for row in table["rows"]}
        assert ("revenue", "FY2024") in metrics
        assert ("revenue", "FY2023") in metrics
        fy24 = next(row for row in table["rows"] if row["period"] == "FY2024")
        cells = {cell["entity"]: cell for cell in fy24["cells"]}
        assert cells["MSFT"]["value"] == "$245,122 million"
        assert cells["MSFT"]["status"] == "ok"

    def test_missing_data_flagged_not_omitted(self):
        table = build_comparison_table(
            [
                fact("AAPL", "total debt", "$100 billion", "FY2024"),
                fact("MSFT", "revenue", "$245 billion", "FY2024"),
            ]
        )
        debt_row = next(r for r in table["rows"] if r["metric"] == "total debt")
        msft_cell = next(c for c in debt_row["cells"] if c["entity"] == "MSFT")
        assert msft_cell["status"] == "missing"
        assert msft_cell["value"] is None
        assert any("missing" in note for note in table["notes"])

    def test_conflicting_values_flagged(self):
        table = build_comparison_table(
            [
                fact("AAPL", "revenue", "$391,035 million", "FY2024", chunk="c1"),
                fact("AAPL", "revenue", "$999,999 million", "FY2024", chunk="c2"),
                fact("MSFT", "revenue", "$245,122 million", "FY2024"),
            ]
        )
        row = next(r for r in table["rows"] if r["period"] == "FY2024")
        aapl_cell = next(c for c in row["cells"] if c["entity"] == "AAPL")
        assert aapl_cell["status"] == "conflict"
        assert sorted(aapl_cell["source_chunk_ids"]) == ["c1", "c2"]

    def test_formatting_difference_not_a_conflict(self):
        table = build_comparison_table(
            [
                fact("AAPL", "revenue", "$100.0 million", "FY2024"),
                fact("AAPL", "revenue", "$100 million", "FY2024"),
                fact("MSFT", "revenue", "$245 million", "FY2024"),
            ]
        )
        row = next(r for r in table["rows"] if r["period"] == "FY2024")
        aapl_cell = next(c for c in row["cells"] if c["entity"] == "AAPL")
        assert aapl_cell["status"] == "ok"

    def test_unit_mismatch_reported(self):
        table = build_comparison_table(
            [
                fact("AAPL", "growth", "$50 billion", "FY2024", "USD"),
                fact("MSFT", "growth", "12%", "FY2024", "%"),
            ]
        )
        row = table["rows"][0]
        assert "%" in (row["note"] or "")
        assert any("mix units" in note for note in table["notes"])

    def test_single_fact_not_warranted(self):
        table = build_comparison_table([fact("AAPL", "revenue", "$1", "FY2024")])
        assert table["warranted"] is False
        assert table["rows"] == []

    def test_compare_agent_node_updates_state(self):
        facts = [
            fact("AAPL", "revenue", "$1", "FY2024").model_dump(),
            fact("MSFT", "revenue", "$2", "FY2024").model_dump(),
        ]
        updates = CompareAgent()({"extracted_facts": facts, "agent_path": ["fetch", "extract"]})
        assert updates["comparison_table"]["warranted"] is True
        assert updates["agent_path"][-1] == "compare"


# --------------------------------------------------------------------------
# Synthesize agent
# --------------------------------------------------------------------------


class TestSynthesizeAgent:
    def _state(self, chunks, facts=None, table=None, unavailable=None):
        return {
            "query": "Compare AAPL and MSFT revenue FY2024",
            "retrieved_chunks": chunks,
            "extracted_facts": facts or [],
            "comparison_table": table,
            "unavailable_sources": unavailable or [],
            "limitations": [],
            "agent_path": ["classify", "fetch", "extract"],
        }

    def test_citation_enforcement_drops_invalid_markers(self):
        chunks = [make_chunk("c1", "AAPL revenue text"), make_chunk("c2", "MSFT revenue text")]
        text = "Apple led with $391B [1]; Microsooft followed [2]. Fabricated claim [9]."
        agent = SynthesizeAgent(engine=script_engine(text))
        updates = agent(self._state(chunks))
        assert updates["final_answer"].startswith("Apple led")
        assert len(updates["citations"]) == 2
        assert {c["chunk_id"] for c in updates["citations"]} == {"c1", "c2"}
        citation = updates["citations"][0]
        assert set(citation) >= {"source_id", "title", "excerpt", "url"}

    def test_insufficient_marker_produces_refusal(self):
        text = f"{INSUFFICIENT_MARKER} The excerpts contain no margin data."
        agent = SynthesizeAgent(engine=script_engine(text))
        updates = agent(self._state([make_chunk("c1", "some text")]))
        assert updates["final_answer"].startswith("I don't have enough indexed evidence")
        assert updates["citations"] == []

    def test_generation_failure_digest_is_grounded_and_cited(self):
        chunks = [make_chunk("c1", "AAPL revenue excerpt")]
        facts = [fact("AAPL", "revenue", "$391,035 million", "FY2024", chunk="c1")]

        class DeadEngine:
            def generate(self, *a, **k):
                raise ProviderUnavailableError("no providers")

        updates = SynthesizeAgent(engine=DeadEngine())(self._state(chunks, facts))
        answer = updates["final_answer"]
        assert "direct digest" in answer
        assert "[1]" in answer
        assert len(updates["citations"]) == 1
        assert updates["citations"][0]["chunk_id"] == "c1"

    def test_no_evidence_names_unavailable_sources(self):
        agent = SynthesizeAgent(engine=script_engine(SYNTH_TEXT))
        updates = agent(self._state([], unavailable=["news_api: unavailable (missing API key)"]))
        assert updates["final_answer"].startswith(REFUSAL_PREFIX)
        assert "news_api" in updates["final_answer"]

    def test_limitations_block_appended_once(self):
        text = "Answer with facts [1]."
        agent = SynthesizeAgent(engine=script_engine(text))
        state = self._state([make_chunk("c1", "evidence")])
        state["limitations"] = ["comparison flagged 1 missing cell"]
        updates = agent(state)
        assert "Limitations:" in updates["final_answer"]
        assert updates["final_answer"].count("Limitations:") == 1

    def test_conflicting_rows_surface_in_answer_context(self):
        table = build_comparison_table(
            [
                fact("AAPL", "revenue", "$1", "FY2024"),
                fact("MSFT", "revenue", None, "FY2024"),
            ]
        )
        state = self._state([make_chunk("c1", "ev")], table=table)
        prompt_capture = {}

        class CapturingEngine:
            def generate(self, prompt, **kwargs):
                prompt_capture["prompt"] = prompt
                raise ProviderUnavailableError("force digest")

        SynthesizeAgent(engine=CapturingEngine())(state)
        assert "MISSING" in prompt_capture["prompt"]


# --------------------------------------------------------------------------
# Graph-level behavior: retries, degradation, full runs
# --------------------------------------------------------------------------


class TestGuardedNodes:
    def test_transient_failure_recovers_within_budget(self):
        attempts = {"n": 0}

        def flaky(state):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("transient boom")
            return {"ok": True}

        wrapped = _guarded("test", flaky, lambda s, e: {})
        assert wrapped({}) == {"ok": True}
        assert attempts["n"] == 2

    def test_permanent_failure_degrades_and_records_error(self):
        def broken(state):
            raise RuntimeError("SECRET-INTERNAL detail")

        wrapped = _guarded("extract", broken, lambda s, e: {"extracted_facts": []}, retries=1)
        updates = wrapped({})
        assert updates["extracted_facts"] == []
        assert updates["node_errors"][0]["node"] == "extract"
        assert updates["node_errors"][0]["recovered"] is True
        assert "SECRET-INTERNAL" not in json.dumps(updates["node_errors"][0]["error"])

    def test_fetch_embed_failure_degrades_to_empty_evidence(self):
        engine = LLMEngine(
            providers=[ScriptedProvider("p", embed_script=[TransientProviderError("down")])]
        )
        agent = FetchAgent(
            engine=engine,
            store=FakeVectorStore(),
            adapters={"sec_filing": FakeAdapter([], name="sec_edgar")},
            settings=Settings(_env_file=None),
        )
        updates = agent(cast("dict", initial_state("Apple revenue")))
        assert updates["retrieved_chunks"] == []
        assert any("embedding" in note for note in updates["limitations"])


class TestFullGraph:
    def _components(self, engine_outputs):
        engine = script_engine(*engine_outputs)
        store = seed_store(
            make_chunk("c-aapl", "Apple total net sales $391,035 million FY2024", ticker="AAPL"),
            make_chunk("c-msft", "Microsoft revenue $245,122 million FY2024", ticker="MSFT"),
        )
        fetch = FetchAgent(
            engine=engine,
            store=store,
            adapters={"sec_filing": FakeAdapter([], name="sec_edgar")},
            settings=Settings(_env_file=None, embedding_dimension=3),
        )
        extract = ExtractAgent(engine=engine)
        compare = CompareAgent()
        synthesize = SynthesizeAgent(engine=engine)
        return engine, store, fetch, extract, compare, synthesize

    def test_multi_entity_run_includes_compare_and_valid_citations(self):
        _, _, fetch, extract, compare, synthesize = self._components(
            [
                FACTS_JSON.replace("AAPL", "MSFT").replace("391,035", "245,122"),
                FACTS_JSON,
                SYNTH_TEXT,
            ]
        )
        service = SentinelQueryService(
            rag_chain=_cast_stub(),
            fetch_agent=fetch,
            extract_agent=extract,
            compare_agent=compare,
            synthesize_agent=synthesize,
            settings=Settings(_env_file=None),
        )
        result = service.answer("Compare AAPL and MSFT revenue FY2024")
        assert result.query_type == "multi_hop"
        assert result.agent_path == ["classify", "fetch", "extract", "compare", "synthesize"]
        assert "[1]" in result.answer
        assert all(c.get("chunk_id") for c in result.citations)

    def test_single_entity_run_skips_compare(self):
        engine = script_engine(FACTS_JSON, SYNTH_TEXT)
        store = seed_store(make_chunk("c1", "Apple total net sales FY2024 $391,035 million"))
        fetch = FetchAgent(
            engine=engine,
            store=store,
            adapters={"sec_filing": FakeAdapter([], name="sec_edgar")},
            settings=Settings(_env_file=None, embedding_dimension=3),
        )
        service = SentinelQueryService(
            rag_chain=_cast_stub(),
            fetch_agent=fetch,
            extract_agent=ExtractAgent(engine=engine),
            compare_agent=CompareAgent(),
            synthesize_agent=SynthesizeAgent(engine=engine),
            settings=Settings(_env_file=None),
        )
        # Force the agent path even though this would classify simple.
        result = service.answer("What was Apple revenue?", force_agents=True)
        assert "compare" not in result.agent_path
        assert result.agent_path[:3] == ["classify", "fetch", "extract"]

    def test_extract_failure_still_grounds_response(self):
        engine = script_engine(InvalidRequestError("bad payload"), SYNTH_TEXT)
        store = seed_store(make_chunk("c1", "Apple revenue $391,035 million"))
        fetch = FetchAgent(
            engine=engine,
            store=store,
            adapters={"sec_filing": FakeAdapter([], name="sec_edgar")},
            settings=Settings(_env_file=None, embedding_dimension=3),
        )
        service = SentinelQueryService(
            rag_chain=_cast_stub(),
            fetch_agent=fetch,
            extract_agent=ExtractAgent(engine=engine),
            compare_agent=CompareAgent(),
            synthesize_agent=SynthesizeAgent(engine=engine),
            settings=Settings(_env_file=None),
        )
        result = service.answer("What was Apple revenue?", force_agents=True)
        assert result.answer  # useful grounded output, no exception
        assert "391,035" in result.answer or "digest" in result.answer

    def test_simple_classification_rides_rag_path(self):
        class StubRag:
            def run(self, question, *, top_k=None, filters=None):
                return RagAnswer(
                    answer="RAG says hi [1].",
                    citations=[{"source_id": "s", "title": "t", "excerpt": "e"}],
                    agent_path=["rewrite", "embed", "retrieve", "generate"],
                )

        _, _, fetch, extract, compare, synthesize = self._components([])
        service = SentinelQueryService(
            rag_chain=StubRag(),  # type: ignore[arg-type]
            fetch_agent=fetch,
            extract_agent=extract,
            compare_agent=compare,
            synthesize_agent=synthesize,
            settings=Settings(_env_file=None),
        )
        result = service.answer("What was Apple's total net sales?")
        assert result.query_type == "simple"
        assert result.agent_path[0] == "classify"
        assert "retrieve" in result.agent_path
        assert "fetch" not in result.agent_path

    def test_tracing_records_agent_spans_via_recording_tracer(self):
        tracer = RecordingTracer(url="mem://trace/phase3")
        engine = script_engine(FACTS_JSON, SYNTH_TEXT)
        store = seed_store(make_chunk("c1", "Apple revenue $391,035 million FY2024"))
        fetch = FetchAgent(
            engine=engine,
            store=store,
            adapters={"sec_filing": FakeAdapter([], name="sec_edgar")},
            settings=Settings(_env_file=None, embedding_dimension=3),
            tracer=tracer,
        )
        service = SentinelQueryService(
            rag_chain=_cast_stub(),
            fetch_agent=fetch,
            extract_agent=ExtractAgent(engine=engine, tracer=tracer),
            compare_agent=CompareAgent(),
            synthesize_agent=SynthesizeAgent(engine=engine, tracer=tracer),
            settings=Settings(_env_file=None),
            tracer=tracer,
        )
        result = service.answer("What was Apple revenue?", force_agents=True)
        names = [entry["name"] for entry in tracer.finished]
        assert "agent_fetch" in names
        assert "agent_extract" in names
        assert "agent_synthesize" in names
        assert result.trace_url == "mem://trace/phase3"

    def test_null_tracer_leaves_trace_url_unset(self):
        _, _, fetch, extract, compare, synthesize = self._components([FACTS_JSON, SYNTH_TEXT])
        service = SentinelQueryService(
            rag_chain=_cast_stub(),
            fetch_agent=fetch,
            extract_agent=extract,
            compare_agent=compare,
            synthesize_agent=synthesize,
            settings=Settings(_env_file=None),
        )
        result = service.answer("What was Apple revenue?", force_agents=True)
        assert result.trace_url is None


def _cast_stub() -> RagChain:
    return cast(RagChain, _StubRag())


class _StubRag:
    """Never invoked on multi-hop paths; placeholder for the service ctor."""

    def run(self, question, *, top_k=None, filters=None):  # pragma: no cover
        raise AssertionError("simple path unexpectedly used")
