"""News API adapter (spec section 6.3) — key-gated, degrades gracefully.

Default provider: Financial Modeling Prep stock-news endpoint
(https://financialmodelingprep.com/api/v3/stock_news). The provider is
configurable via settings.news_api_provider / adapters.yaml (news_api.provider);
new providers register in _PROVIDERS with an endpoint and a parser.

fetch({ticker | keywords, date_range, page, limit}) pages the provider until
the result limit is reached or the feed is exhausted, normalizing each entry:

- RawDocument.source_id is deterministic — NEWS:{PROVIDER}:{SYMBOL}:{hash} — so
  re-fetching the same article upserts instead of duplicating evidence.
- A secondary content hash (normalized title + published date) drops syndicated
  duplicates inside one fetch.
- title/url/publisher/author/published date/ticker/raw text are preserved;
  provider HTML in content is stripped to plain text without mangling
  financial figures ("$1.2 billion", "12.5%", "FY2024" survive intact).

Failure semantics (spec 6.3): a missing/blank key means is_available() == False
and callers skip the source; malformed payloads yield zero documents rather
than exceptions where sensible; transient HTTP failures retry with exponential
backoff honoring Retry-After (clamped), while authentication (401/403) and
invalid-request (400/404) errors surface immediately. The API key travels only
inside request params and is never logged — log lines carry scheme+host+path
and status codes only.
"""

import hashlib
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from urllib.parse import SplitResult, urlsplit

import requests

from config.settings import Settings, get_settings, resolve_secret
from data_sources.base import DataSourceAdapter
from models.schemas import RawDocument

logger = logging.getLogger(__name__)

FMP_NEWS_URL = "https://financialmodelingprep.com/api/v3/stock_news"

# Transient statuses worth a bounded retry; auth (401/403) and invalid-request
# (400/404) errors never retry — the same call would fail again.
_RETRYABLE_STATUSES = {408, 429, 502, 503, 504}
_MAX_RETRY_WAIT_SECONDS = 60.0

_TAG_RE = re.compile(r"<[^>]+>")

# Loop protection for pagination: providers that ignore the `page` param would
# otherwise return the same window forever.
_MAX_PAGES_PER_FETCH = 10

# Module-level indirection so offline tests patch sleeps out.
_sleep = time.sleep


# --------------------------------------------------------------------------
# Pure helpers (unit-testable without HTTP)
# --------------------------------------------------------------------------


def sanitize_text(text: str | None, *, max_chars: int = 20_000) -> str:
    """Provider HTML -> clean plain text. Tags stripped, entities unescaped,
    whitespace collapsed. Financial wording ($ figures, %, FY labels, units)
    passes through untouched."""
    if not text:
        return ""
    plain = _TAG_RE.sub(" ", text)
    plain = unescape(plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:max_chars].rstrip()


def parse_published_date(value: Any) -> date | None:
    """Accept FMP's 'YYYY-MM-DD HH:MM:SS', ISO datetimes/dates, or None.
    Unparseable values degrade to None, never raise."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None


def normalize_symbol(symbol: Any) -> str:
    """Uppercased ticker symbol or '' when absent/malformed."""
    if not isinstance(symbol, str):
        return ""
    cleaned = symbol.strip().upper()
    return cleaned if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", cleaned) else ""


def content_hash(title: str, published_iso: str | None) -> str:
    """Syndication-insensitive fingerprint: normalized title + published day."""
    normalized = re.sub(r"[^a-z0-9]+", "", title.lower())
    return hashlib.sha256(f"{normalized}|{published_iso or ''}".encode()).hexdigest()[:16]


def article_source_id(provider: str, symbol: str, url: str, fallback_key: str) -> str:
    """Deterministic document id. Provider article URLs are stable, so the URL
    hash makes refetches idempotent; entries without a URL fall back to a hash
    of the caller-supplied key (title + date)."""
    key = url.strip().lower() if url and url.strip() else f"url-less:{fallback_key}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return f"NEWS:{provider.upper()}:{symbol or 'GENERAL'}:{digest}"


@dataclass
class _ParsedArticle:
    source_id: str
    content_hash_value: str
    document: RawDocument


def parse_fmp_articles(
    payload: Any,
    *,
    provider: str = "financial_modeling_prep",
    ticker_hint: str | None = None,
) -> list[RawDocument]:
    """FMP stock-news payload -> deduplicated RawDocuments.

    Accepts only a list of objects; anything else (error dicts, strings,
    None) yields []. Entries missing BOTH title and content are skipped —
    there is nothing meaningful to index. Missing url/author/site/date are
    tolerated individually.
    """
    if not isinstance(payload, list):
        return []

    documents: list[RawDocument] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()

    for entry in payload:
        if not isinstance(entry, dict):
            continue
        title = sanitize_text(entry.get("title"), max_chars=500)
        body = sanitize_text(entry.get("text") or entry.get("content"))
        if not title and not body:
            continue
        headline = title or body[:120]

        raw_url = entry.get("url")
        url: str = strip_query_string(raw_url) if isinstance(raw_url, str) else ""
        published = parse_published_date(entry.get("publishedDate"))
        published_iso = published.isoformat() if published else None
        symbol: str = normalize_symbol(entry.get("symbol")) or (
            ticker_hint.upper() if ticker_hint else ""
        )

        hash_value = content_hash(headline, published_iso)
        if hash_value in seen_hashes:
            continue  # syndicated duplicate of an earlier entry
        source_id = article_source_id(provider, symbol, url, hash_value)
        if source_id in seen_ids:
            continue

        seen_hashes.add(hash_value)
        seen_ids.add(source_id)
        documents.append(
            RawDocument(
                source_id=source_id,
                source_type="news",
                title=headline,
                published_date=published,
                raw_text=f"{title}\n\n{body}".strip() if title else body,
                metadata={
                    "ticker": symbol or None,
                    "publisher": sanitize_text(entry.get("site"), max_chars=200) or None,
                    "author": sanitize_text(entry.get("author"), max_chars=200) or None,
                    "url": url or None,
                    "provider": provider,
                    "title": headline,
                    "published_date": published_iso,
                    "date": published_iso,
                },
            )
        )
    return documents


def _normalize_date_range(value: Any) -> tuple[str | None, str | None]:
    """None or [start, end] of ISO strings/dates -> (start_iso, end_iso)."""
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


def strip_query_string(url: str) -> str:
    """URL safe to log: scheme+host+path only — query strings here always
    carry the API key."""
    parts: SplitResult = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


class NewsApiRequestError(RuntimeError):
    """Provider request failed (non-retryable status, or retries exhausted).

    Raised in place of the raw ``requests`` exception because requests embeds
    the full request URL — query string included, and our query strings carry
    the API key — in its message, which propagates into pipeline failure
    records, API error envelopes, and logs.
    """


# --------------------------------------------------------------------------
# Provider registry (extensible; spec 6.3 keeps this configurable)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderSpec:
    """One news provider's endpoint + parser."""

    name: str
    endpoint: str
    build_params: Callable[[dict], dict]
    parse: Callable[..., list[RawDocument]]  # parsers take keyword hints


def _fmp_build_params(params: dict) -> dict:
    """FMP stock-news parameters from canonical fetch params (no apikey here —
    the adapter injects it last so it can never be logged)."""
    query: dict = {"page": params.get("page", 0)}
    if params.get("_ticker"):
        query["tickers"] = params["_ticker"]
    if params.get("_keywords"):
        query["q"] = params["_keywords"]
    if params.get("_start"):
        query["from"] = params["_start"]
    if params.get("_end"):
        query["to"] = params["_end"]
    return query


_PROVIDERS: dict[str, ProviderSpec] = {
    "financial_modeling_prep": ProviderSpec(
        name="financial_modeling_prep",
        endpoint=FMP_NEWS_URL,
        build_params=_fmp_build_params,
        parse=parse_fmp_articles,
    ),
}


# --------------------------------------------------------------------------
# Adapter
# --------------------------------------------------------------------------


class NewsApiAdapter(DataSourceAdapter):
    """News via a configurable HTTP provider; unavailable without a key."""

    name = "news_api"

    # session is duck-typed (requests.Session live, a fake in tests) — typed Any.
    def __init__(
        self,
        settings: Settings | None = None,
        session: Any = None,
        *,
        provider: str | None = None,
    ):
        self.settings = settings or get_settings()
        self.provider = provider or self.settings.news_api_provider
        spec = _PROVIDERS.get(self.provider)
        if spec is None:
            raise ValueError(
                f"Unknown news provider {self.provider!r}; registered: {sorted(_PROVIDERS)}"
            )
        self._spec = spec
        self.session = session or requests.Session()
        self.timeout_seconds = 15
        self.max_retries = 2
        self.backoff_base_seconds = 0.5
        # FMP serves 20 items/page; used to detect a short (final) page.
        self.page_size = 20

    def is_available(self) -> bool:
        """Key presence check only — cheap and side-effect free per the base
        contract. An invalid-but-present key surfaces at fetch() time, where
        callers already degrade gracefully (spec 6.3)."""
        return bool((resolve_secret(self.settings.news_api_key) or "").strip())

    # -- public entry point -------------------------------------------------

    def fetch(self, query_params: dict) -> list[RawDocument]:
        params = dict(query_params)
        ticker = str(params["ticker"]).upper().strip() if params.get("ticker") else ""
        keywords = str(params["keywords"]).strip() if params.get("keywords") else ""
        if not ticker and not keywords:
            raise ValueError("fetch() requires 'ticker' or 'keywords' in query_params")
        start, end = _normalize_date_range(params.get("date_range"))
        limit = max(int(params.get("limit", 20)), 1)
        page = max(int(params.get("page", 0)), 0)

        documents: list[RawDocument] = []
        current_page = page
        previous_first_id: str | None = None
        while len(documents) < limit and current_page < page + _MAX_PAGES_PER_FETCH:
            payload = self._get_json(
                self._spec.endpoint,
                params=self._request_params(
                    {
                        "page": current_page,
                        "_ticker": ticker,
                        "_keywords": keywords,
                        "_start": start,
                        "_end": end,
                    }
                ),
            )
            articles = self._spec.parse(payload, ticker_hint=ticker or None)
            if not articles:
                break  # exhausted, malformed, or empty page — return what we have
            if previous_first_id is not None and articles[0].source_id == previous_first_id:
                logger.info(
                    "News provider %s ignored page parameter; stopping pagination",
                    self.provider,
                )
                break
            previous_first_id = articles[0].source_id

            for document in articles:
                if len(documents) >= limit:
                    break
                documents.append(document)
            current_page += 1
            if len(articles) < self.page_size:
                break
        return documents

    def _request_params(self, fetch_params: dict) -> dict:
        """Provider params + credentials. The apikey is added here, at the last
        moment, so no other code path handles or logs it."""
        query = self._spec.build_params(fetch_params)
        # Resolved from SecretStr here, at the last moment, so no other code
        # path handles or logs the raw value.
        query["apikey"] = resolve_secret(self.settings.news_api_key)
        return query

    # -- HTTP with bounded retries ---------------------------------------------

    def _retry_delay(self, attempt: int, retry_after: str | None) -> float:
        """Honor Retry-After (seconds or HTTP-date) clamped; else exponential
        backoff — mirrors SecEdgarAdapter._retry_delay."""
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
            delay = 0.0
            try:
                response = self.session.get(url, params=params, timeout=self.timeout_seconds)
                response.raise_for_status()
                return response
            except requests.HTTPError as exc:
                status = getattr(exc.response, "status_code", None)
                if status not in _RETRYABLE_STATUSES or attempt == self.max_retries:
                    # Never re-raise: str(exc) embeds the credentialed URL.
                    raise NewsApiRequestError(
                        f"news provider returned HTTP {status} for {strip_query_string(url)}"
                    ) from exc
                headers = getattr(exc.response, "headers", None)
                retry_after = headers.get("Retry-After") if headers is not None else None
                delay = self._retry_delay(attempt, retry_after)
                logger.warning(
                    "News provider returned HTTP %s for %s; retry %d/%d in %.1fs",
                    status,
                    strip_query_string(url),
                    attempt + 1,
                    self.max_retries,
                    delay,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt == self.max_retries:
                    # ConnectionError messages can also embed the full URL.
                    raise NewsApiRequestError(
                        f"{type(exc).__name__} after {self.max_retries + 1} "
                        f"attempts for {strip_query_string(url)}"
                    ) from exc
                delay = self._retry_delay(attempt, None)
                logger.warning(
                    "News request failed (%s) for %s; retry %d/%d in %.1fs",
                    type(exc).__name__,
                    strip_query_string(url),
                    attempt + 1,
                    self.max_retries,
                    delay,
                )
            _sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def _get_json(self, url: str, params: dict | None = None) -> Any:
        return self._get(url, params=params).json()
