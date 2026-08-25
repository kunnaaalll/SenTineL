"""SEC EDGAR adapter (spec section 6.2) — public, key-less, always available.

How the spec's `fetch({ticker, filing_type, date_range})` maps onto EDGAR's
actual API surface:

- Ticker -> CIK resolution:  https://www.sec.gov/files/company_tickers.json
- Filing listing:            https://data.sec.gov/submissions/CIK{cik:010d}.json
  (parallel arrays under filings.recent; filtered here by form + filingDate)
- Full-text keyword search:  https://efts.sec.gov/LATEST/search-index
  (used when a `query` is given instead of a ticker)
- Document download:         https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{file}

SEC requires a descriptive User-Agent carrying contact information and caps
request rate around 10 req/s; this adapter sends both and self-throttles.

Filing documents are HTML. They are converted to plain text here with tables
rendered as markdown pipe-table blocks — that is the input format the
financial chunker expects (tables atomic, prose splittable).
"""

import logging
import re
import time
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import requests
from bs4 import BeautifulSoup, NavigableString

from config.settings import Settings, get_settings, is_placeholder_contact_email
from data_sources.base import DataSourceAdapter
from models.schemas import RawDocument

logger = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
EFTS_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodashes}/{filename}"

# Transient statuses worth a bounded retry; anything else (403 ban, 404, ...)
# surfaces immediately. SEC aggressively bans abusers — never hammer on 403.
_RETRYABLE_STATUSES = {408, 429, 502, 503, 504}
_MAX_RETRY_WAIT_SECONDS = 60.0  # clamp pathological Retry-After values


class SecContactEmailConfigError(ValueError):
    """Live EDGAR use attempted while SEC_CONTACT_EMAIL is still a placeholder."""


# Module-level indirection so offline tests can patch sleeps out.
_sleep = time.sleep

_DROP_TAGS = ("script", "style")
# Block-level elements get a trailing newline so get_text() keeps line structure
# without breaking inline formatting mid-sentence.
_BLOCK_TAGS = [
    "p",
    "div",
    "tr",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "table",
    "section",
    "article",
    "header",
    "footer",
]


# --------------------------------------------------------------------------
# HTML -> chunker-ready text
# --------------------------------------------------------------------------


def _table_to_markdown(table) -> str:
    """Render a <table> as markdown pipe rows. Nested tables flatten into the
    enclosing cell's text."""
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = [
            " ".join(cell.get_text(" ", strip=True).split()) for cell in tr.find_all(["td", "th"])
        ]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(r) + " |" for r in rows]
    lines.insert(1, "|" + " --- |" * width)  # markdown separator row
    return "\n".join(lines)


def html_to_financial_text(html: str) -> str:
    """Strip scripts/styles, render top-level tables as markdown blocks,
    collapse whitespace. Output feeds directly into financial_chunker."""
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.find_all(_DROP_TAGS):
        el.decompose()

    # Only outermost tables become markdown blocks; nested ones are absorbed
    # into their parent cell by _table_to_markdown's cell extraction.
    for table in soup.find_all("table"):
        if table.find_parent("table") is not None:
            continue
        markdown = _table_to_markdown(table)
        if markdown:
            table.replace_with(NavigableString("\n\n" + markdown + "\n\n"))
        else:
            table.decompose()

    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.append("\n")

    text = soup.get_text()
    lines: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"[ \t\xa0]+", " ", line).strip()
        if line or (lines and lines[-1]):  # collapse blank runs to one
            lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


# --------------------------------------------------------------------------
# EDGAR JSON parsing (pure functions — unit-testable without HTTP)
# --------------------------------------------------------------------------


def parse_company_tickers(payload: dict) -> dict[str, str]:
    """{"0": {"cik_str": 320193, "ticker": "AAPL", ...}} -> {"AAPL": "320193"}"""
    return {str(entry["ticker"]).upper(): str(entry["cik_str"]) for entry in payload.values()}


def select_filings(
    submissions: dict,
    filing_type: str | None,
    start: str | None,
    end: str | None,
    limit: int | None = None,
) -> list[dict]:
    """Filter the parallel arrays in submissions["filings"]["recent"] down to
    filings of `filing_type` whose filingDate is within [start, end] inclusive.
    Date bounds are ISO strings compared lexicographically (safe for ISO dates)."""
    recent = submissions.get("filings", {}).get("recent", {})
    cik = str(submissions.get("cik", ""))
    forms = recent.get("form", [])

    def field(name: str, i: int) -> str:
        """Read position i of a parallel array; EDGAR arrays are normally
        equal-length, but a ragged response degrades to "" not IndexError."""
        arr = recent.get(name) or []
        return arr[i] if i < len(arr) else ""

    out: list[dict] = []
    for i, form in enumerate(forms):
        if filing_type and form.upper() != filing_type.upper():
            continue
        filed = field("filingDate", i)
        if start and filed < start:
            continue
        if end and filed > end:
            continue
        out.append(
            {
                "cik": cik,
                "form": form,
                "accession_number": field("accessionNumber", i),
                "filing_date": filed,
                "report_date": field("reportDate", i) or None,
                "primary_document": field("primaryDocument", i),
                "primary_doc_description": field("primaryDocDescription", i),
            }
        )
        if limit and len(out) >= limit:
            break
    return out


def parse_efts_hits(payload: dict) -> list[dict]:
    """Flatten EDGAR full-text-search hits into filing dicts shaped like
    select_filings() output. hit id format: '{accession}:{filename}'."""
    hits = payload.get("hits", {}).get("hits", [])
    out = []
    for hit in hits:
        src = hit.get("_source", {})
        doc_id = src.get("_id") or hit.get("_id", "")
        accession, _, filename = doc_id.partition(":")
        ciks = src.get("ciks") or []
        forms = src.get("form") or []
        names = src.get("display_names") or []
        out.append(
            {
                "cik": str(ciks[0]) if ciks else "",
                "form": str(forms[0]) if forms else None,
                "accession_number": accession,
                "filing_date": src.get("file_date"),
                "report_date": None,
                "primary_document": filename,
                "company_name": names[0] if names else None,
            }
        )
    return out


def _normalize_date_range(value) -> tuple[str | None, str | None]:
    """Accept None, a 2-sequence of ISO strings/dates -> (start_iso, end_iso)."""
    if not value:
        return None, None
    items = list(value)
    if len(items) != 2:
        raise ValueError("date_range must be [start, end]")
    start, end = items
    return (
        start.isoformat() if isinstance(start, date) else start,
        end.isoformat() if isinstance(end, date) else end,
    )


# --------------------------------------------------------------------------
# Adapter
# --------------------------------------------------------------------------


class SecEdgarAdapter(DataSourceAdapter):
    name = "sec_edgar"

    # session is duck-typed (requests.Session live, a fake in tests) — typed Any.
    def __init__(self, settings: Settings | None = None, session: Any = None):
        self.settings = settings or get_settings()
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.settings.sec_user_agent,
                "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
            }
        )
        self.timeout_seconds = 30
        # Bounded retry policy for transient failures (audit risk #4): SEC
        # returns Retry-After on throttling and we honor it, clamped to a sane
        # ceiling so a hostile header can't stall ingestion for minutes.
        self.max_retries = 2
        self.backoff_base_seconds = 0.5
        self._min_interval = 0.12  # ~8 req/s, under SEC's ~10 req/s cap
        self._last_request_at = 0.0
        self._tickers: dict[str, str] | None = None  # ticker -> CIK, lazily loaded

    def is_available(self) -> bool:
        return True  # public source, no key (spec section 6.2)

    # -- public entry point -------------------------------------------------

    def fetch(self, query_params: dict) -> list[RawDocument]:
        # SEC fair-access gate: no live EDGAR request may leave while the
        # User-Agent still carries the placeholder contact — that is exactly
        # the traffic pattern SEC bans IPs for. Raised before any network I/O;
        # surfaces via pipeline failures / agent unavailable_sources with this
        # message intact.
        if is_placeholder_contact_email(self.settings.sec_contact_email):
            raise SecContactEmailConfigError(
                "SEC_CONTACT_EMAIL is not configured with a real address. SEC "
                "fair-access policy requires a descriptive User-Agent carrying a "
                "genuine contact before live EDGAR use. Set SEC_CONTACT_EMAIL "
                "(see .env.example) and retry."
            )

        params = dict(query_params)
        ticker = params.get("ticker")
        query = params.get("query")
        filing_type = params.get("filing_type")
        start, end = _normalize_date_range(params.get("date_range"))
        limit = int(params.get("limit", 10))
        if not ticker and not query:
            raise ValueError("fetch() requires 'ticker' or 'query' in query_params")

        if ticker:
            filings = self._filings_by_ticker(ticker, filing_type, start, end)
        else:
            filings = self._filings_by_full_text_search(query, filing_type, start, end)

        docs: list[RawDocument] = []
        for filing in filings[:limit]:
            docs.append(self._to_raw_document(filing, ticker_hint=ticker))
        return docs

    # -- fetch paths ---------------------------------------------------------

    def _filings_by_ticker(self, ticker, filing_type, start, end) -> list[dict]:
        cik = self._resolve_ticker(ticker)
        submissions = self._get_json(SUBMISSIONS_URL.format(cik=int(cik)))
        filings = select_filings(submissions, filing_type, start, end)
        # Real submissions JSON carries the registrant name at the top level;
        # thread it through so document titles show the company, not the ticker.
        company_name = submissions.get("name")
        if company_name:
            for filing in filings:
                filing.setdefault("company_name", company_name)
        return filings

    def _filings_by_full_text_search(self, query, filing_type, start, end) -> list[dict]:
        efts_params: dict = {"q": f'"{query}"'}
        if filing_type:
            efts_params["forms"] = filing_type.upper()
        if start:
            efts_params["startdt"] = start
        if end:
            efts_params["enddt"] = end
        return parse_efts_hits(self._get_json(EFTS_SEARCH_URL, params=efts_params))

    def _resolve_ticker(self, ticker: str) -> str:
        tickers = self._ticker_map()
        try:
            return tickers[ticker.upper()]
        except KeyError:
            raise ValueError(f"Unknown ticker {ticker!r} in SEC company_tickers.json") from None

    def _ticker_map(self) -> dict[str, str]:
        if self._tickers is None:
            self._tickers = parse_company_tickers(self._get_json(TICKERS_URL))
        return self._tickers

    def _cik_to_ticker(self, cik: str) -> str | None:
        cik_normalized = str(int(cik)) if cik else ""
        for t, c in self._ticker_map().items():
            if c.lstrip("0") == cik_normalized:
                return t
        return None

    # -- document construction ----------------------------------------------

    def _to_raw_document(self, filing: dict, ticker_hint: str | None) -> RawDocument:
        cik = filing["cik"]
        accession_nodashes = filing["accession_number"].replace("-", "")
        url = ARCHIVES_URL.format(
            cik=int(cik), accession_nodashes=accession_nodashes, filename=filing["primary_document"]
        )
        html = self._get_text(url)

        form = filing.get("form") or "UNKNOWN"
        filed = filing.get("filing_date")
        ticker = ticker_hint or self._cik_to_ticker(str(cik)) or str(cik)
        company = filing.get("company_name") or ticker

        return RawDocument(
            source_id=f"SEC:{ticker}:{form}:{filed or 'unknown'}",
            source_type="sec_filing",
            title=f"{company} {form} filed {filed}" if filed else f"{company} {form}",
            published_date=date.fromisoformat(filed) if filed else None,
            raw_text=html_to_financial_text(html),
            metadata={
                "ticker": ticker,
                "cik": str(cik),
                "form": form,
                "accession_number": filing["accession_number"],
                "report_date": filing.get("report_date"),
                "company_name": company,
                "url": url,
            },
        )

    # -- HTTP with rate limiting + bounded retries -----------------------------

    def _throttle(self) -> None:
        wait = self._min_interval - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _retry_delay(self, attempt: int, retry_after: str | None) -> float:
        """Honor Retry-After (seconds or HTTP-date) when present, clamped;
        otherwise exponential backoff."""
        if retry_after:
            try:
                return min(float(retry_after), _MAX_RETRY_WAIT_SECONDS)
            except ValueError:
                pass
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                wait = (retry_at - datetime.now(UTC)).total_seconds()
                return min(max(wait, 0.0), _MAX_RETRY_WAIT_SECONDS)
            except (TypeError, ValueError):
                pass
        return min(self.backoff_base_seconds * (2**attempt), _MAX_RETRY_WAIT_SECONDS)

    def _get(self, url: str, params: dict | None = None) -> requests.Response:
        for attempt in range(self.max_retries + 1):
            self._throttle()
            delay = 0.0
            try:
                response = self.session.get(url, params=params, timeout=self.timeout_seconds)
                response.raise_for_status()
                return response
            except requests.HTTPError as exc:
                status = getattr(exc.response, "status_code", None)
                if status not in _RETRYABLE_STATUSES or attempt == self.max_retries:
                    raise
                retry_after = None
                headers = getattr(exc.response, "headers", None)
                if headers is not None:
                    retry_after = headers.get("Retry-After")
                delay = self._retry_delay(attempt, retry_after)
                logger.warning(
                    "SEC returned HTTP %s for %s; retry %d/%d in %.1fs",
                    status,
                    url,
                    attempt + 1,
                    self.max_retries,
                    delay,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt == self.max_retries:
                    raise
                delay = self._retry_delay(attempt, None)
                logger.warning(
                    "SEC request failed (%s) for %s; retry %d/%d in %.1fs",
                    type(exc).__name__,
                    url,
                    attempt + 1,
                    self.max_retries,
                    delay,
                )
            _sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def _get_json(self, url: str, params: dict | None = None) -> dict:
        return self._get(url, params=params).json()

    def _get_text(self, url: str) -> str:
        return self._get(url).text
