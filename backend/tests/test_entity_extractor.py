"""Phase 2 entity-extraction tests (offline, deterministic pass + mocked LLM path)."""

from fakes import ScriptedProvider

from ingestion.entity_extractor import (
    MAX_ENTITIES_PER_CHUNK,
    ExtractedEntities,
    enrich_chunk,
    extract_entities,
    extract_entities_llm,
    merge_entities,
    parse_json_object,
)
from llm_providers.engine import LLMEngine
from models.schemas import Chunk


def extract(text: str, **kwargs) -> ExtractedEntities:
    return extract_entities(text, **kwargs)


def flat(text: str, **kwargs) -> list[str]:
    return extract(text, **kwargs).flatten()


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------


class TestMoney:
    def test_simple_dollar_figure(self):
        assert extract("Revenue was $391,035 for the year.").money == ["$391,035"]

    def test_decimal_and_negative(self):
        assert "$1,234.56" in extract("Costs rose to $1,234.56.").money
        assert "($500)" in extract("The charge of ($500) was booked.").money

    def test_scale_words_normalized(self):
        entities = extract("Sales grew $ 2.5 billion while costs hit $3 Million.")
        assert "$2.5 billion" in entities.money
        assert "$3 million" in entities.money

    def test_single_letter_suffix_uppercased(self):
        assert "$5B" in extract("Capex approached $5B in 2024.").money

    def test_no_false_positive_from_bare_numbers(self):
        assert extract("The count was 42 units.").money == []


# --------------------------------------------------------------------------
# Percentages / dates / periods
# --------------------------------------------------------------------------


class TestPercentagesDatesPeriods:
    def test_percentage_forms(self):
        result = extract("Margins hit 12.5%, up from (8%).")
        assert "12.5%" in result.percentages
        assert "(8%)" in result.percentages or "8%" in result.percentages

    def test_iso_and_text_dates(self):
        result = extract("Filed on 2024-11-01 and reported November 15, 2024.")
        assert "2024-11-01" in result.dates
        assert any("November 15, 2024" in d for d in result.dates)

    def test_fiscal_periods_canonicalized(self):
        result = extract(
            "For fiscal year 2024 and FY 2025 guidance; Q3 2024 was strong, as was Q4FY2024."
        )
        normalized = set(result.periods)
        assert "FY2024" in normalized
        assert "FY2025" in normalized
        assert "Q3-2024" in normalized
        assert "Q4-2024" in normalized

    def test_fiscal_year_word_form(self):
        result = extract("Results for fiscal 2024 exceeded plan.")
        assert "FY2024" in result.periods


# --------------------------------------------------------------------------
# Tickers
# --------------------------------------------------------------------------


class TestTickers:
    def test_cashtag_detected_case_insensitive(self):
        assert extract("$aapl rallied.").tickers == ["AAPL"]

    def test_exchange_prefix_detected(self):
        assert "MSFT" in extract("As listed on NASDAQ: MSFT, the company...").tickers

    def test_builtin_ticker_requires_uppercase_token(self):
        assert "AAPL" not in extract("apple shipped phones.").tickers
        assert extract("AAPL vs MSFT comparison.").tickers == ["AAPL", "MSFT"]

    def test_known_tickers_argument_extends_detection(self):
        assert "SNOW" in extract("SNOW reported growth.", known_tickers=["snow"]).tickers

    def test_single_letter_cashtag_ignored_to_avoid_money_collision(self):
        # "$5B" is money, not ticker B.
        assert "B" not in extract("up $5B year over year").tickers

    def test_dedup_preserves_order(self):
        assert extract("MSFT then AAPL then MSFT again.").tickers == ["MSFT", "AAPL"]


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


class TestMetrics:
    def test_metric_with_money_figure(self):
        result = extract("Total net sales were $391,035 in fiscal 2024.")
        assert any(metric.startswith("total net sales:") for metric in result.metrics)

    def test_metric_with_percentage_figure(self):
        result = extract("Gross margin reached 46.2% this quarter.")
        assert "gross margin: 46.2%" in result.metrics

    def test_eps_alias(self):
        result = extract("EPS of $6.08 beat consensus.")
        assert any(metric.startswith("eps:") for metric in result.metrics)


# --------------------------------------------------------------------------
# Flatten / chunk enrichment / caps
# --------------------------------------------------------------------------


class TestFlattenAndEnrichment:
    def test_flatten_priority_order_and_dedupe(self):
        entities = ExtractedEntities(
            tickers=["AAPL"], dates=["2024-09-28"], money=["$10"], percentages=["5%"]
        )
        assert entities.flatten() == ["AAPL", "2024-09-28", "$10", "5%"]

    def test_flatten_caps_at_max_entities(self):
        entities = ExtractedEntities(money=[f"${i}" for i in range(MAX_ENTITIES_PER_CHUNK + 20)])
        assert len(entities.flatten()) == MAX_ENTITIES_PER_CHUNK

    def _chunk(self, text: str, ticker: str | None = None) -> Chunk:
        return Chunk(
            chunk_id="c1",
            source_id="SEC:TEST:10-K:2024-11-01",
            source_type="sec_filing",
            text=text,
            metadata={"ticker": ticker} if ticker else {},
        )

    def test_enrich_chunk_sets_entities_including_doc_ticker(self):
        chunk = self._chunk("Revenue was $100,000, up 5%.", ticker="ZZZT")
        enrich_chunk(chunk)
        assert "ZZZT" in chunk.entities
        assert "$100,000" in chunk.entities
        assert "5%" in chunk.entities

    def test_empty_text_yields_no_entities(self):
        chunk = self._chunk("")
        enrich_chunk(chunk)
        assert chunk.entities == []


# --------------------------------------------------------------------------
# JSON parsing + LLM-assisted merge
# --------------------------------------------------------------------------


class TestJsonParsing:
    def test_plain_json(self):
        assert parse_json_object('{"a": 1}') == {"a": 1}

    def test_code_fence_wrapped(self):
        assert parse_json_object('```json\n{"a": 2}\n```') == {"a": 2}

    def test_surrounding_prose(self):
        assert parse_json_object('Sure! Here it is:\n{"a": 3}\nDone.') == {"a": 3}

    def test_non_object_raises(self):
        import pytest

        with pytest.raises(ValueError):
            parse_json_object("[1, 2, 3]")
        with pytest.raises(ValueError):
            parse_json_object("no json here")


class TestMergeAndLLMPath:
    def test_merge_adds_validated_entries_only(self):
        baseline = extract("Revenue was $100.")
        merged = merge_entities(
            baseline,
            {
                "money": ["$200"],
                "tickers": ["tsla"],
                "junk_key": ["x"],
                "dates": [42, "2024-01-01"],
            },
        )
        assert "$100" in merged.money and "$200" in merged.money
        assert "TSLA" in merged.tickers
        assert "2024-01-01" in merged.dates
        # unknown keys dropped entirely
        assert not hasattr(merged, "junk_key")

    def _engine_with_reply(self, reply: str) -> LLMEngine:
        provider = ScriptedProvider("p", generation_script=[reply])
        return LLMEngine(providers=[provider])

    def test_llm_extraction_merges_over_baseline(self):
        engine = self._engine_with_reply('{"tickers": ["NVDA"], "money": ["$16B"]}')
        merged = extract_entities_llm("Revenue was $100 at NVDA.", engine)
        assert "NVDA" in merged.tickers
        assert "$16b" in [m.lower() for m in merged.money] or any(
            m.lower().startswith("$16") for m in merged.money
        )
        assert "$100" in merged.money  # baseline preserved

    def test_llm_failure_falls_back_to_deterministic(self):
        engine = self._engine_with_reply("not json at all")
        merged = extract_entities_llm("Revenue was $100.", engine)
        assert merged.money == ["$100"]
        assert merged.tickers == []

    def test_llm_provider_unavailable_falls_back_silently(self):
        provider = ScriptedProvider("p", available=False)
        engine = LLMEngine(providers=[provider])
        merged = extract_entities_llm("Gross profit hit $50.", engine)
        assert "$50" in merged.money
