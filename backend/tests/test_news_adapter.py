"""News API adapter tests (offline).

Fake HTTP sessions return queued responses; sleeps are patched out — no real
news provider, no network. Covers normalization, sanitization, dedup,
pagination bounds, retry/backoff/Retry-After behavior, auth-error non-retry,
malformed payloads, key-absent unavailability, and key-never-logged hygiene.
"""

import logging
from typing import Any

import pytest
import requests

import data_sources.news_api as news_module
from config.settings import Settings
from data_sources.news_api import (
    NewsApiAdapter,
    content_hash,
    normalize_symbol,
    parse_fmp_articles,
    parse_published_date,
    sanitize_text,
    strip_query_string,
)

# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, *, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)  # type: ignore[arg-type]


class FakeNewsSession:
    """Queued responses; records the params of every call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "timeout": timeout})
        return self.responses.pop(0)


@pytest.fixture()
def no_sleep(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(news_module, "_sleep", lambda seconds: sleeps.append(seconds))
    return sleeps


def make_adapter(session, **overrides) -> NewsApiAdapter:
    settings = Settings(_env_file=None, **{"news_api_key": "test-key-123", **overrides})
    return NewsApiAdapter(settings=settings, session=session)


def article(
    symbol="AAPL",
    title="Apple beats revenue estimates",
    published="2025-01-15 09:30:00",
    site="Seeking Alpha",
    author="Jane Reporter",
    url="https://example.com/aapl-beats",
    content=None,
) -> dict:
    return {
        "symbol": symbol,
        "title": title,
        "publishedDate": published,
        "site": site,
        "author": author,
        "url": url,
        "content": content
        if content is not None
        else "<p>Apple reported <b>revenue</b> of $124.3 billion, up 4% year over year "
        "in Q1 FY2025.</p>",
    }


def ok_page(*entries) -> FakeResponse:
    return FakeResponse(status_code=200, json_data=list(entries))


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


class TestPureHelpers:
    def test_sanitize_strips_html_preserves_financial_text(self):
        dirty = "<p>Revenue rose to $124.3 billion (+12.5%), EPS $2.18 in Q1&nbsp;FY2025.</p>"
        clean = sanitize_text(dirty)
        assert "<p>" not in clean and "<b>" not in clean
        assert "$124.3 billion" in clean
        assert "12.5%" in clean
        assert "Q1 FY2025" in clean
        assert "\n" not in clean and "  " not in clean

    def test_sanitize_handles_none_and_empty(self):
        assert sanitize_text(None) == ""
        assert sanitize_text("") == ""

    def test_parse_published_date_formats(self):
        assert str(parse_published_date("2025-01-15 09:30:00")) == "2025-01-15"
        assert str(parse_published_date("2025-01-15")) == "2025-01-15"
        assert parse_published_date("garbage") is None
        assert parse_published_date("") is None
        assert parse_published_date(None) is None
        assert parse_published_date(12345) is None

    def test_normalize_symbol(self):
        assert normalize_symbol("aapl") == "AAPL"
        assert normalize_symbol("BRK.B") == "BRK.B"
        assert normalize_symbol("not a symbol!") == ""
        assert normalize_symbol(None) == ""
        assert normalize_symbol(42) == ""

    def test_content_hash_ignores_case_and_punctuation(self):
        assert (
            content_hash("Apple Beats Estimates!", "2025-01-15")
            == content_hash("apple beats estimates", "2025-01-15")
            != content_hash("apple beats estimates", "2025-01-16")
        )

    def test_source_id_deterministic_url_vs_fallback(self):
        first = news_module.article_source_id("fmp", "AAPL", "https://x.com/a", "fb1")
        assert first == news_module.article_source_id("fmp", "AAPL", "https://x.com/a", "zzz")
        assert first.startswith("NEWS:FMP:AAPL:")
        fallback_only = news_module.article_source_id("fmp", "", "", "fb1")
        assert fallback_only != first and "GENERAL" in fallback_only

    def test_strip_query_string_removes_credentials(self):
        logged = strip_query_string("https://host.example/api/v3/stock_news?apikey=SECRET&page=1")
        assert "SECRET" not in logged
        assert logged == "https://host.example/api/v3/stock_news"


# --------------------------------------------------------------------------
# Payload parsing / normalization / dedup
# --------------------------------------------------------------------------


class TestParseArticles:
    def test_full_normalization_preserves_required_fields(self):
        docs = parse_fmp_articles([article()])
        assert len(docs) == 1
        doc = docs[0]
        assert doc.source_type == "news"
        assert doc.source_id.startswith("NEWS:FINANCIAL_MODELING_PREP:AAPL:")
        assert doc.title == "Apple beats revenue estimates"
        assert doc.published_date is not None and str(doc.published_date) == "2025-01-15"
        # raw text keeps headline + sanitized body with figures intact
        assert "revenue" in doc.raw_text and "$124.3 billion" in doc.raw_text
        meta = doc.metadata
        assert meta["ticker"] == "AAPL"
        assert meta["publisher"] == "Seeking Alpha"
        assert meta["author"] == "Jane Reporter"
        assert meta["url"] == "https://example.com/aapl-beats"
        assert meta["date"] == "2025-01-15"
        assert meta["provider"] == "financial_modeling_prep"

    def test_exact_duplicate_entries_collapse_to_one_document(self):
        entry = article()
        docs = parse_fmp_articles([entry, dict(entry)])
        assert len(docs) == 1

    def test_syndicated_duplicate_same_title_and_day_collapses(self):
        original = article()
        syndicated = article(url="https://other-outlet.com/same-story")
        docs = parse_fmp_articles([original, syndicated])
        assert len(docs) == 1

    def test_malformed_payloads_yield_empty_list_not_exceptions(self):
        payloads: tuple[Any, ...] = (None, {"Error Message": "bad key"}, "nope", 42, [])
        for bad in payloads:
            assert parse_fmp_articles(bad) == []

    def test_partial_entries_handled_individually(self):
        payload = [
            "not-a-dict",
            7,
            {},  # nothing usable
            {"title": "  ", "content": "   "},  # whitespace-only
            {"content": "<i>Body-only story about $5B buyback.</i>"},
            {"title": "Title-only headline"},
            article(),
        ]
        docs = parse_fmp_articles(payload)
        assert len(docs) == 3
        body_only = next(d for d in docs if d.title.startswith("Body-only"))
        assert "$5B buyback" in body_only.raw_text
        assert body_only.metadata["publisher"] is None
        assert body_only.metadata["url"] is None
        title_only = next(d for d in docs if d.title == "Title-only headline")
        assert title_only.published_date is None

    def test_missing_symbol_falls_back_to_hint_or_general(self):
        hinted = parse_fmp_articles([article(symbol="")], ticker_hint="msft")[0]
        assert hinted.metadata["ticker"] == "MSFT"
        general = parse_fmp_articles([article(symbol="", title="Markets rally")])[0]
        assert "GENERAL" in general.source_id


# --------------------------------------------------------------------------
# Availability
# --------------------------------------------------------------------------


class TestAvailability:
    def test_missing_key_means_unavailable(self):
        adapter = make_adapter(FakeNewsSession([]), news_api_key="")
        assert adapter.is_available() is False

    def test_blank_key_means_unavailable(self):
        adapter = make_adapter(FakeNewsSession([]), news_api_key="   ")
        assert adapter.is_available() is False

    def test_present_key_is_available_without_network(self):
        assert make_adapter(FakeNewsSession([])).is_available() is True

    def test_unknown_provider_rejected_at_construction(self):
        with pytest.raises(ValueError, match="Unknown news provider"):
            make_adapter(FakeNewsSession([]), news_api_provider="does_not_exist")


# --------------------------------------------------------------------------
# Fetch: params, pagination, limits
# --------------------------------------------------------------------------


def page_entries(count, *, base_title="Story"):
    return [
        article(title=f"{base_title} {i}", url=f"https://example.com/{base_title}-{i}")
        for i in range(count)
    ]


class TestFetch:
    def test_ticker_and_keywords_reach_provider_params_with_key_last(self):
        session = FakeNewsSession([ok_page(*page_entries(2))])
        docs = make_adapter(session).fetch({"ticker": "aapl", "keywords": "earnings", "limit": 2})
        assert len(docs) == 2
        sent = session.calls[0]["params"]
        assert sent["tickers"] == "AAPL"
        assert sent["q"] == "earnings"
        assert sent["apikey"] == "test-key-123"  # present exactly once
        assert sent["page"] == 0

    def test_date_range_maps_to_from_to(self):
        session = FakeNewsSession([ok_page(article())])
        make_adapter(session).fetch({"ticker": "AAPL", "date_range": ["2025-01-01", "2025-01-31"]})
        sent = session.calls[0]["params"]
        assert sent["from"] == "2025-01-01"
        assert sent["to"] == "2025-01-31"

    def test_requires_ticker_or_keywords(self):
        with pytest.raises(ValueError, match="ticker.*or.*keywords"):
            make_adapter(FakeNewsSession([])).fetch({})

    def test_limit_respected_across_pages(self):
        session = FakeNewsSession(
            [ok_page(*page_entries(20)), ok_page(*page_entries(20, base_title="More"))]
        )
        docs = make_adapter(session).fetch({"ticker": "AAPL", "limit": 25})
        assert len(docs) == 25
        assert len(session.calls) == 2
        assert session.calls[1]["params"]["page"] == 1

    def test_short_page_signals_exhaustion(self):
        session = FakeNewsSession([ok_page(*page_entries(3))])
        docs = make_adapter(session).fetch({"ticker": "AAPL", "limit": 50})
        assert len(docs) == 3
        assert len(session.calls) == 1  # short page -> no second request

    def test_provider_ignoring_page_param_does_not_loop_forever(self):
        same = ok_page(*page_entries(20))
        session = FakeNewsSession([same, same, same])
        docs = make_adapter(session).fetch({"ticker": "AAPL", "limit": 100})
        assert len(docs) <= 40  # stopped after detecting an identical window
        assert len(session.calls) == 2

    def test_pagination_hard_cap_bounds_requests(self):
        # Full 20-entry pages with distinct content: neither the short-page
        # rule nor the repeated-window guard fires, so only the hard cap can
        # stop pagination.
        pages = [ok_page(*page_entries(20, base_title=f"Page{i}")) for i in range(12)]
        session = FakeNewsSession(pages)
        docs = make_adapter(session).fetch({"ticker": "AAPL", "limit": 500})
        assert len(docs) == 10 * 20  # hard stop after _MAX_PAGES_PER_FETCH pages
        assert len(session.calls) == news_module._MAX_PAGES_PER_FETCH


# --------------------------------------------------------------------------
# HTTP failure semantics
# --------------------------------------------------------------------------


class TestHttpFailureSemantics:
    def test_rate_limit_retries_honoring_retry_after_seconds(self, no_sleep):
        throttled = FakeResponse(status_code=429, headers={"Retry-After": "7"})
        session = FakeNewsSession([throttled, ok_page(article())])
        docs = make_adapter(session).fetch({"ticker": "AAPL"})
        assert len(docs) == 1
        assert len(session.calls) == 2
        assert no_sleep == [7.0]

    def test_server_error_retries_with_exponential_backoff(self, no_sleep):
        session = FakeNewsSession(
            [
                FakeResponse(status_code=503),
                FakeResponse(status_code=502),
                ok_page(article()),
            ]
        )
        docs = make_adapter(session).fetch({"ticker": "AAPL"})
        assert len(docs) == 1
        assert no_sleep == [0.5, 1.0]

    def test_timeout_retries_then_succeeds(self, no_sleep, monkeypatch):
        attempts = {"n": 0}

        class FlakySession(FakeNewsSession):
            def get(self, url, params=None, timeout=None):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise requests.Timeout("too slow")
                return super().get(url, params=params, timeout=timeout)

        docs = make_adapter(FlakySession([ok_page(article())])).fetch({"ticker": "AAPL"})
        assert len(docs) == 1
        assert no_sleep == [0.5]

    def test_authentication_error_never_retried(self, no_sleep):
        session = FakeNewsSession([FakeResponse(status_code=401)] * 5)
        with pytest.raises(requests.HTTPError, match="401"):
            make_adapter(session).fetch({"ticker": "AAPL"})
        assert len(session.calls) == 1  # surfaced immediately
        assert no_sleep == []

    def test_forbidden_error_never_retried(self, no_sleep):
        session = FakeNewsSession([FakeResponse(status_code=403)] * 5)
        with pytest.raises(requests.HTTPError, match="403"):
            make_adapter(session).fetch({"ticker": "AAPL"})
        assert len(session.calls) == 1

    def test_invalid_request_400_never_retried(self, no_sleep):
        session = FakeNewsSession([FakeResponse(status_code=400)] * 5)
        with pytest.raises(requests.HTTPError, match="400"):
            make_adapter(session).fetch({"ticker": "AAPL"})
        assert len(session.calls) == 1

    def test_retry_after_http_date_clamped(self, no_sleep):
        from datetime import datetime, timedelta

        soon = datetime.now() + timedelta(hours=6)
        header = soon.strftime("%a, %d %b %Y %H:%M:%S GMT")
        session = FakeNewsSession(
            [FakeResponse(status_code=429, headers={"Retry-After": header}), ok_page(article())]
        )
        docs = make_adapter(session).fetch({"ticker": "AAPL"})
        assert len(docs) == 1
        assert no_sleep == [news_module._MAX_RETRY_WAIT_SECONDS]

    def test_malformed_json_payload_returns_documents_not_crash(self):
        session = FakeNewsSession([FakeResponse(status_code=200, json_data={"error": "oops"})])
        docs = make_adapter(session).fetch({"ticker": "AAPL"})
        assert docs == []


# --------------------------------------------------------------------------
# Secret hygiene
# --------------------------------------------------------------------------


class TestSecretHygiene:
    def test_api_key_never_appears_in_logs(self, no_sleep, caplog):
        caplog.set_level(logging.DEBUG, logger=news_module.__name__)
        session = FakeNewsSession(
            [
                FakeResponse(status_code=503),
                FakeResponse(status_code=429, headers={"Retry-After": "1"}),
                FakeResponse(status_code=401),
            ]
        )
        with pytest.raises(requests.HTTPError):
            make_adapter(session).fetch({"ticker": "AAPL"})
        assert caplog.text
        assert "test-key-123" not in caplog.text
        assert "apikey" not in caplog.text
