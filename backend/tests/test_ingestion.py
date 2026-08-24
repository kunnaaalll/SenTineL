"""Phase 1 ingestion tests (spec section 15).

Covers the financial chunker (tables atomic, prose sentence-boundary
splitting with overlap, footnotes as metadata, section tagging) and the SEC
EDGAR adapter (EDGAR JSON parsing, HTML->text conversion, fetch flow against
a fake HTTP session — no network access).
"""

from datetime import date

import pytest

from data_sources.sec_edgar import (
    SecEdgarAdapter,
    html_to_financial_text,
    parse_company_tickers,
    parse_efts_hits,
    select_filings,
)
from ingestion.financial_chunker import (
    TARGET_CHARS,
    chunk_document,
    pack_sentences,
    split_blocks,
    split_sentences,
)
from models.schemas import RawDocument

# --------------------------------------------------------------------------
# Helpers / fixtures
# --------------------------------------------------------------------------


def _sentence(i: int) -> str:
    return (
        f"Sentence number {i} discusses revenue growth of {i * 3} percent "
        f"this fiscal year for the reporting entity."
    )


def _prose_doc(n_sentences: int, ticker: str = "TEST") -> RawDocument:
    text = " ".join(_sentence(i) for i in range(1, n_sentences + 1))
    return RawDocument(
        source_id=f"SEC:{ticker}:10-K:2024-12-31",
        source_type="sec_filing",
        title=f"{ticker} 10-K",
        published_date=date(2024, 12, 31),
        raw_text=text,
        metadata={"ticker": ticker},
    )


def _markdown_table(rows: int, cols: int = 4) -> str:
    header = "| " + " | ".join(f"Account {c}" for c in range(cols)) + " |"
    separator = "|" + " --- |" * cols
    body = [
        "| " + " | ".join(f"Line {r}-{c} $1,23{c}" for c in range(cols)) + " |" for r in range(rows)
    ]
    return "\n".join([header, separator] + body)


TICKERS_PAYLOAD = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
}

SUBMISSIONS_PAYLOAD = {
    "cik": 320193,
    "name": "Apple Inc.",
    "filings": {
        "recent": {
            "accessionNumber": [
                "0000320193-24-000123",
                "0000320193-24-000099",
                "0000320193-23-000077",
            ],
            "filingDate": ["2024-11-01", "2024-08-02", "2023-11-03"],
            "reportDate": ["2024-09-28", "2024-06-30", "2023-09-30"],
            "form": ["10-K", "8-K", "10-K"],
            "primaryDocument": ["aapl-20240928.htm", "8k-body.htm", "aapl-20230930.htm"],
            "primaryDocDescription": ["10-K", "8-K", "10-K"],
        }
    },
}

FILING_HTML = """<html><head><script>tracker()</script><style>.x {{}}</style></head><body>
<div>Item 7. Management's Discussion and Analysis</div>
<p>Apple fiscal 2024 revenue was strong across every segment. iPhone led growth in services.</p>
<table><tr><th>Item</th><th>FY2024</th></tr>
<tr><td>Total net sales</td><td>$391,035</td></tr>
<tr><td>Net income</td><td>$93,736</td></tr></table>
<p>(1) Includes deferred revenue and other adjustments as described below.</p>
</body></html>"""

EFTS_PAYLOAD = {
    "hits": {
        "hits": [
            {
                "_id": "0000320193-24-000123:aapl-20240928.htm",
                "_source": {
                    "_id": "0000320193-24-000123:aapl-20240928.htm",
                    "ciks": ["320193"],
                    "display_names": ["Apple Inc."],
                    "form": ["10-K"],
                    "file_date": "2024-11-01",
                },
            }
        ]
    }
}


class FakeResponse:
    def __init__(self, json_data=None, text=""):
        self._json = json_data
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        return None


class FakeSession:
    """Routes GET requests to canned responses by URL substring."""

    def __init__(self, routes: dict[str, FakeResponse]):
        self.routes = routes
        self.headers: dict[str, str] = {}
        self.calls: list[tuple] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        for fragment, response in self.routes.items():
            if fragment in url:
                return response
        raise AssertionError(f"No fake route matches {url}")


def _edgar_routes() -> dict[str, FakeResponse]:
    return {
        "company_tickers.json": FakeResponse(json_data=TICKERS_PAYLOAD),
        "submissions/CIK": FakeResponse(json_data=SUBMISSIONS_PAYLOAD),
        "search-index": FakeResponse(json_data=EFTS_PAYLOAD),
        "Archives/edgar": FakeResponse(text=FILING_HTML),
    }


def _adapter(session: FakeSession) -> SecEdgarAdapter:
    adapter = SecEdgarAdapter(session=session)
    adapter._min_interval = 0.0  # no throttle in tests
    return adapter


# --------------------------------------------------------------------------
# HTML -> chunker-ready text
# --------------------------------------------------------------------------


class TestHtmlToFinancialText:
    def test_tables_become_markdown_blocks(self):
        out = html_to_financial_text(FILING_HTML)
        assert "| Item | FY2024 |" in out
        assert "| --- | --- |" in out
        assert "| Total net sales | $391,035 |" in out
        assert "| Net income | $93,736 |" in out

    def test_script_and_style_dropped(self):
        out = html_to_financial_text(FILING_HTML)
        assert "tracker()" not in out
        assert ".x" not in out

    def test_nested_table_flattens_into_parent_cell(self):
        html = (
            "<table><tr><td>Outer</td>"
            "<td><table><tr><td>Inner A</td><td>Inner B</td></tr></table></td>"
            "</tr></table>"
        )
        out = html_to_financial_text(html)
        assert "<table" not in out
        assert "Inner A" in out and "Outer" in out


# --------------------------------------------------------------------------
# Chunker: prose splitting
# --------------------------------------------------------------------------


class TestChunkerProse:
    def test_splits_long_prose_near_target(self):
        chunks = chunk_document(_prose_doc(60))
        assert len(chunks) >= 4
        assert all(len(c.text) <= TARGET_CHARS for c in chunks)

    def test_consecutive_chunks_share_overlap_sentence(self):
        pieces = pack_sentences(split_sentences(_prose_doc(60).raw_text))
        assert len(pieces) >= 2
        for prev, nxt in zip(pieces, pieces[1:], strict=False):
            last_sentence = prev.split(". ")[-1]
            assert prev.endswith(last_sentence)
            assert nxt.startswith(last_sentence)

    def test_short_document_is_single_chunk(self):
        chunks = chunk_document(_prose_doc(5))
        assert len(chunks) == 1
        assert chunks[0].text.endswith(".")

    def test_full_sentence_coverage(self):
        chunks = chunk_document(_prose_doc(40))
        covered = " ".join(c.text for c in chunks)
        for i in range(1, 41):
            assert _sentence(i) in covered

    def test_chunk_ids_deterministic_and_unique(self):
        first = chunk_document(_prose_doc(30))
        second = chunk_document(_prose_doc(30))
        ids = [c.chunk_id for c in first]
        assert len(ids) == len(set(ids))
        assert ids == [c.chunk_id for c in second]


# --------------------------------------------------------------------------
# Chunker: tables stay atomic
# --------------------------------------------------------------------------


class TestTables:
    def _doc_with_table(self, rows: int) -> RawDocument:
        table = _markdown_table(rows)
        text = (
            f"Intro paragraph before the table. {_sentence(1)}\n\n"
            f"{table}\n\n"
            f"Paragraph after the table. {_sentence(2)}"
        )
        doc = _prose_doc(1)
        doc.raw_text = text
        return doc

    def test_large_table_stays_atomic(self):
        rows = 40  # well over TARGET_CHARS
        doc = self._doc_with_table(rows=rows)
        chunks = chunk_document(doc)
        table_chunks = [c for c in chunks if c.page_or_position.startswith("table")]
        assert len(table_chunks) == 1
        table_text = table_chunks[0].text
        assert table_text.count("\n") == rows + 1  # header + separator + rows, newline-joined
        assert table_text.startswith("| Account 0")
        assert table_text.endswith("|")

    def test_table_not_merged_with_prose(self):
        doc = self._doc_with_table(rows=3)
        chunks = chunk_document(doc)
        assert len(chunks) == 3
        assert chunks[1].page_or_position.startswith("table")
        assert "|" not in chunks[0].text and "|" not in chunks[2].text

    def test_table_chunk_page_or_position_marks_table(self):
        doc = self._doc_with_table(rows=2)
        chunk = [c for c in chunk_document(doc) if "|" in c.text][0]
        assert chunk.page_or_position.startswith("table chars ")


# --------------------------------------------------------------------------
# Chunker: footnotes attach as metadata, never standalone
# --------------------------------------------------------------------------


class TestFootnotes:
    def _doc(self, body: str) -> RawDocument:
        doc = _prose_doc(1)
        doc.raw_text = body
        return doc

    def test_footnote_attaches_to_previous_chunk(self):
        doc = self._doc(
            "Main discussion paragraph about operations.\n\n"
            "(1) Includes goodwill allocated to the reporting unit."
        )
        chunks = chunk_document(doc)
        assert len(chunks) == 1
        assert chunks[0].footnotes == ["(1) Includes goodwill allocated to the reporting unit."]

    def test_leading_footnote_attaches_to_first_chunk(self):
        doc = self._doc(
            "(1) Amounts presented reflect reclassifications.\n\n"
            "Business overview paragraph with actual content."
        )
        chunks = chunk_document(doc)
        assert len(chunks) == 1
        assert chunks[0].footnotes and "reclassifications" in chunks[0].footnotes[0]

    def test_lowercase_continuation_absorbed_into_footnote(self):
        doc = self._doc(
            "Primary narrative paragraph for the period.\n\n"
            "(1) Includes deferred revenue adjustments.\n\n"
            "these amounts exclude intercompany eliminations made during consolidation."
        )
        chunks = chunk_document(doc)
        assert len(chunks) == 1
        assert len(chunks[0].footnotes) == 2

    def test_uppercase_paragraph_not_absorbed(self):
        doc = self._doc(
            "Primary narrative paragraph for the period.\n\n"
            "(1) Includes deferred revenue adjustments.\n\n"
            "Separately the company disclosed an outlook for next year."
        )
        chunks = chunk_document(doc)
        assert len(chunks) == 2
        assert chunks[0].footnotes
        assert "Separately" in chunks[1].text
        assert not chunks[1].footnotes


# --------------------------------------------------------------------------
# Chunker: sections, metadata flow
# --------------------------------------------------------------------------


class TestSectionsAndMetadata:
    def test_item_headings_set_sections(self):
        text = (
            "Item 7. Management's Discussion and Analysis\n\n"
            f"{_sentence(1)} {_sentence(2)}\n\n"
            "Item 1A. Risk Factors\n\n"
            f"{_sentence(3)} {_sentence(4)}"
        )
        doc = _prose_doc(1)
        doc.raw_text = text
        chunks = chunk_document(doc)
        assert chunks[0].section == "Item 7 - Management's Discussion and Analysis"
        assert chunks[1].section == "Item 1A - Risk Factors"

    def test_document_metadata_flows_to_chunks(self):
        chunks = chunk_document(_prose_doc(10))
        for chunk in chunks:
            assert chunk.metadata["ticker"] == "TEST"
            assert chunk.metadata["title"] == "TEST 10-K"
            assert chunk.metadata["published_date"] == "2024-12-31"
            assert chunk.source_id == "SEC:TEST:10-K:2024-12-31"
            assert chunk.entities == []

    def test_blocks_splitter_kinds(self):
        blocks = split_blocks(f"{_sentence(1)}\n\n{_markdown_table(2)}\n\nmore prose here.")
        kinds = [b.kind for b in blocks]
        assert kinds == ["prose", "table", "prose"]


# --------------------------------------------------------------------------
# SEC EDGAR: pure parsing functions
# --------------------------------------------------------------------------


class TestSecEdgarParsing:
    def test_parse_company_tickers(self):
        assert parse_company_tickers(TICKERS_PAYLOAD) == {"AAPL": "320193", "MSFT": "789019"}

    def test_select_filings_filters_form_and_date(self):
        filings = select_filings(SUBMISSIONS_PAYLOAD, "10-K", "2024-01-01", "2024-12-31")
        assert len(filings) == 1
        assert filings[0]["accession_number"] == "0000320193-24-000123"
        assert filings[0]["filing_date"] == "2024-11-01"
        assert filings[0]["primary_document"] == "aapl-20240928.htm"

    def test_select_filings_date_bounds_inclusive(self):
        filings = select_filings(SUBMISSIONS_PAYLOAD, "10-K", "2023-11-03", "2024-11-01")
        assert {f["filing_date"] for f in filings} == {"2023-11-03", "2024-11-01"}

    def test_select_filings_no_type_returns_all(self):
        assert len(select_filings(SUBMISSIONS_PAYLOAD, None, None, None)) == 3

    def test_select_filings_ragged_arrays_do_not_raise(self):
        ragged = {
            "cik": 320193,
            "filings": {
                "recent": {
                    "form": ["10-K", "8-K"],
                    "accessionNumber": ["0000320193-24-000123"],  # shorter than form
                    "filingDate": ["2024-11-01", "2024-08-02"],
                }
            },
        }
        filings = select_filings(ragged, None, None, None)
        assert len(filings) == 2
        assert filings[0]["accession_number"] == "0000320193-24-000123"
        assert filings[1]["accession_number"] == ""

    def test_parse_efts_hits(self):
        filings = parse_efts_hits(EFTS_PAYLOAD)
        assert len(filings) == 1
        assert filings[0]["cik"] == "320193"
        assert filings[0]["accession_number"] == "0000320193-24-000123"
        assert filings[0]["primary_document"] == "aapl-20240928.htm"
        assert filings[0]["filing_date"] == "2024-11-01"
        assert filings[0]["company_name"] == "Apple Inc."

    def test_normalize_date_range_variants(self):
        from data_sources.sec_edgar import _normalize_date_range

        assert _normalize_date_range(None) == (None, None)
        assert _normalize_date_range(["2024-01-01", "2024-12-31"]) == ("2024-01-01", "2024-12-31")
        assert _normalize_date_range([date(2024, 1, 1), date(2024, 12, 31)]) == (
            "2024-01-01",
            "2024-12-31",
        )
        with pytest.raises(ValueError):
            _normalize_date_range(["2024-01-01"])


# --------------------------------------------------------------------------
# SEC EDGAR: fetch flow against a fake session (no network)
# --------------------------------------------------------------------------


class TestSecEdgarFetch:
    def test_fetch_by_ticker_builds_raw_document(self):
        adapter = _adapter(FakeSession(_edgar_routes()))
        docs = adapter.fetch(
            {"ticker": "AAPL", "filing_type": "10-K", "date_range": ["2024-01-01", "2024-12-31"]}
        )
        assert len(docs) == 1
        doc = docs[0]
        assert doc.source_id == "SEC:AAPL:10-K:2024-11-01"
        assert doc.source_type == "sec_filing"
        assert doc.published_date == date(2024, 11, 1)
        assert doc.title == "Apple Inc. 10-K filed 2024-11-01"
        assert doc.metadata["ticker"] == "AAPL"
        assert doc.metadata["cik"] == "320193"
        assert (
            doc.metadata["url"]
            == "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm"
        )
        # HTML was converted: markdown table present, tags gone
        assert "| Total net sales | $391,035 |" in doc.raw_text
        assert "<table" not in doc.raw_text

    def test_descriptive_user_agent_configured(self):
        session = FakeSession(_edgar_routes())
        _adapter(session)
        ua = session.headers["User-Agent"]
        assert "Sentinel" in ua
        assert "@" in ua  # SEC requires contact info in the UA string

    def test_limit_is_respected(self):
        adapter = _adapter(FakeSession(_edgar_routes()))
        docs = adapter.fetch({"ticker": "AAPL", "filing_type": "10-K", "limit": 1})
        assert len(docs) == 1

    def test_unknown_ticker_raises(self):
        adapter = _adapter(FakeSession(_edgar_routes()))
        with pytest.raises(ValueError):
            adapter.fetch({"ticker": "ZZZZ"})

    def test_missing_ticker_and_query_raises(self):
        adapter = _adapter(FakeSession({}))
        with pytest.raises(ValueError):
            adapter.fetch({"filing_type": "10-K"})

    def test_full_text_search_path(self):
        session = FakeSession(_edgar_routes())
        adapter = _adapter(session)
        docs = adapter.fetch(
            {
                "query": "annual report",
                "filing_type": "10-K",
                "date_range": ["2024-01-01", "2024-12-31"],
            }
        )
        assert len(docs) == 1
        # CIK reverse-mapped back to a ticker for the source_id convention
        assert docs[0].source_id == "SEC:AAPL:10-K:2024-11-01"
        # the EFTS request carried the quoted query + form + date window
        search_calls = [c for c in session.calls if "search-index" in c[0]]
        assert search_calls and search_calls[0][1]["q"] == '"annual report"'
        assert search_calls[0][1]["forms"] == "10-K"
        assert search_calls[0][1]["startdt"] == "2024-01-01"

    def test_fetch_then_chunk_integration(self):
        adapter = _adapter(FakeSession(_edgar_routes()))
        docs = adapter.fetch({"ticker": "AAPL", "filing_type": "10-K"})
        chunks = chunk_document(docs[0])
        assert len(chunks) == 2
        assert chunks[0].section == "Item 7 - Management's Discussion and Analysis"
        table_chunk = chunks[1]
        assert table_chunk.page_or_position.startswith("table chars")
        assert "$391,035" in table_chunk.text
        assert table_chunk.footnotes == [
            "(1) Includes deferred revenue and other adjustments as described below."
        ]
        # filterable metadata made it onto every chunk for vector indexing
        assert all(c.metadata["ticker"] == "AAPL" for c in chunks)
