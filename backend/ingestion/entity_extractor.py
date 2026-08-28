"""Financial entity extraction (spec section 7, step 3).

Deterministic regex pass FIRST — tickers, dates, dollar figures, percentages,
and common financial metrics are pulled out of chunk text with zero LLM
dependency, so ingestion is reproducible and free by default. An optional
LLM-assisted path (`extract_entities_llm`, gated behind
settings.enable_llm_entity_extraction) can add recall on top; its output is
merged only after validation, and any failure falls back to the regex-only
result.

`Chunk.entities` stays a flat list[str] (spec section 5): normalized values
like "AAPL", "FY2024", "$391,035", "12.5%", "2024-09-28",
"revenue: $391,035" — used for filtering/boosting at retrieval time.
"""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from models.schemas import Chunk

MAX_ENTITIES_PER_CHUNK = 64

# --------------------------------------------------------------------------
# Patterns (applied to whitespace-normalized chunk text)
# --------------------------------------------------------------------------

_MONTHS = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)

_MONEY_RE = re.compile(
    r"\(\s?\$-?\d+(?:,\d{3})*(?:\.\d+)?\s?\)"  # accounting negatives: ($1,234)
    r"|\$\s?-?\d+(?:,\d{3})*(?:\.\d+)?\s?(?:trillion|billion|million|thousand|[KMBT])?\b",
    re.IGNORECASE,
)
_PCT_RE = re.compile(r"\(?\d+(?:\.\d+)?\s?%\)?")
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_TEXT_DATE_RE = re.compile(rf"\b(?:{_MONTHS})\.?\s+\d{{1,2}},?\s+\d{{4}}\b")
_PERIOD_RE = re.compile(
    r"\bFY\s?'?\d{4}\b"
    r"|\bfiscal\s+(?:year\s+)?\d{4}\b"
    r"|\bQ[1-4]\s?(?:FY'?)?\s?\d{4}\b",
    re.IGNORECASE,
)
_EXCHANGE_TICKER_RE = re.compile(r"\b(?:NASDAQ|NYSE|AMEX|OTC)\s*:\s*([A-Z][A-Z.]{0,5})\b")
# Cashtags need 2+ letters so "$5B"-style money suffixes never match.
_DOLLAR_TICKER_RE = re.compile(r"\$([A-Za-z]{2,5})\b")

# Metric keyword + adjacent figure -> "revenue: $391,035"-style entities.
_METRIC_KEYWORDS = (
    "total net sales|net sales|revenue|net income|operating income|operating expenses|"
    "gross profit|gross margin|operating margin|net margin|earnings per share|EPS|"
    "dividend|free cash flow|capital expenditures|cash flow from operations|"
    "total debt|long-term debt|cash and cash equivalents|cash and equivalents|"
    "total assets|total liabilities|shareholders.?equity|stockholders.?equity|EBITDA|EBIT|guidance"
)
_METRIC_FIGURE = rf"(?:{_MONEY_RE.pattern}|{_PCT_RE.pattern})"
_METRIC_RE = re.compile(
    rf"\b({_METRIC_KEYWORDS})\b[^|$%()\-0-9]{{0,40}}?({_METRIC_FIGURE})",
    re.IGNORECASE,
)

# Mega-cap starter set for bare-ticker detection; callers extend it with the
# document's own ticker via `known_tickers`. Bare tokens must be ALL-CAPS in
# the source text to count (lowercase words like "all" never match).
BUILTIN_TICKERS: frozenset[str] = frozenset(
    {
        "AAPL",
        "MSFT",
        "GOOG",
        "GOOGL",
        "AMZN",
        "META",
        "NVDA",
        "TSLA",
        "AVGO",
        "BRK.A",
        "BRK.B",
        "JPM",
        "V",
        "MA",
        "UNH",
        "JNJ",
        "WMT",
        "PG",
        "HD",
        "KO",
        "PEP",
        "MRK",
        "ABBV",
        "CVX",
        "XOM",
        "BA",
        "DIS",
        "CSCO",
        "NFLX",
        "INTC",
        "AMD",
        "QCOM",
        "ORCL",
        "CRM",
        "IBM",
        "GS",
        "MS",
        "C",
        "BAC",
        "F",
        "GM",
        "NKE",
        "MCD",
        "SBUX",
        "PYPL",
        "ADBE",
        "TXN",
        "CAT",
        "GE",
    }
)

COMPANY_ALIASES: dict[str, str] = {
    "apple": "AAPL",
    "microsoft": "MSFT",
    "nvidia": "NVDA",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "amazon": "AMZN",
    "meta": "META",
    "facebook": "META",
    "tesla": "TSLA",
    "netflix": "NFLX",
    "intel": "INTC",
    "amd": "AMD",
    "qualcomm": "QCOM",
    "oracle": "ORCL",
    "salesforce": "CRM",
    "broadcom": "AVGO",
    "berkshire": "BRK.A",
    "jpmorgan": "JPM",
    "jp morgan": "JPM",
    "visa": "V",
    "mastercard": "MA",
    "walmart": "WMT",
    "disney": "DIS",
    "cisco": "CSCO",
    "boeing": "BA",
    "nike": "NKE",
    "coca cola": "KO",
    "coca-cola": "KO",
    "pepsi": "PEP",
    "pepsico": "PEP",
}


@dataclass
class ExtractedEntities:
    """Typed result of one extraction pass over a text span."""

    tickers: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    periods: list[str] = field(default_factory=list)
    money: list[str] = field(default_factory=list)
    percentages: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)

    def flatten(self) -> list[str]:
        """Deduplicated, order-preserving union in priority order."""
        seen: set[str] = set()
        ordered: list[str] = []
        for value in (
            self.tickers + self.periods + self.dates + self.money + self.percentages + self.metrics
        ):
            key = value.strip()
            if key and key.lower() not in {s.lower() for s in seen}:
                seen.add(key)
                ordered.append(key)
        return ordered[:MAX_ENTITIES_PER_CHUNK]


def _normalize_money(value: str) -> str:
    """Collapse spacing variants ("$ 1,235 billion") into canonical form:
    word suffixes lowercase ("$1.2 billion"), single-letter scale uppercase
    ("$5B"), accounting negatives keep parentheses ("($500)")."""
    compact = re.sub(r"\s+", "", value)
    match = re.match(
        r"^(\(\s?)?(\$-?[\d,.]+)(trillion|billion|million|thousand|[KMBT])(\)?)$",
        compact,
        re.IGNORECASE,
    )
    if match:
        prefix, amount, suffix, close = match.groups()
        if len(suffix) > 1:
            return f"{prefix or ''}{amount} {suffix.lower()}{close or ''}"
        return f"{prefix or ''}{amount}{suffix.upper()}{close or ''}"
    return compact


def _normalize_period(raw: str) -> str:
    """Canonical period labels: "fiscal year 2024"/"FY 2024" -> "FY2024",
    "Q3 2024"/"Q3 FY2024" -> "Q3-2024"."""
    compact = re.sub(r"\s+", "", raw.upper()).replace("'", "")
    compact = compact.replace("FISCALYEAR", "FY").replace("FISCAL", "FY")
    quarter = re.match(r"^(Q[1-4])(?:FY)?(\d{4})$", compact)
    if quarter:
        return f"{quarter.group(1)}-{quarter.group(2)}"
    return compact


def extract_entities(text: str, *, known_tickers: Iterable[str] | None = None) -> ExtractedEntities:
    """Deterministic extraction pass. No I/O, no LLM, order-stable."""
    result = ExtractedEntities()
    if not text:
        return result

    result.money = [_normalize_money(m) for m in _dedupe(_MONEY_RE.findall(text))]
    result.percentages = _dedupe(p.strip() for p in _PCT_RE.findall(text))
    result.dates = _dedupe(_ISO_DATE_RE.findall(text) + _TEXT_DATE_RE.findall(text))
    result.periods = _dedupe(_normalize_period(p) for p in _PERIOD_RE.findall(text))

    tickers: list[str] = []
    tickers.extend(m.group(1).upper() for m in _EXCHANGE_TICKER_RE.finditer(text))
    tickers.extend(m.group(1).upper() for m in _DOLLAR_TICKER_RE.finditer(text))

    known = BUILTIN_TICKERS | {t.upper() for t in (known_tickers or ())}
    if known:
        alternation = "|".join(
            re.escape(t) for t in sorted(known, key=len, reverse=True) if t.isalpha()
        )
        if alternation:
            # Whole-word, case-sensitive: only already-uppercased tokens count.
            for m in re.finditer(rf"\b({alternation})\b", text):
                tickers.append(m.group(1))

    result.tickers = _dedupe(tickers)
    result.metrics = _dedupe(
        f"{metric.lower()}: {figure}" for metric, figure in _METRIC_RE.findall(text)
    )
    return result


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def enrich_chunk(chunk: Chunk, *, known_tickers: Iterable[str] | None = None) -> Chunk:
    """Fill chunk.entities with the deterministic pass; returns the same chunk.

    The document's own ticker (chunk.metadata["ticker"]) is always attached,
    even when the body text never spells it out, so every chunk of an AAPL
    filing is retrievable by "AAPL".
    """
    doc_tickers = (
        [str(chunk.metadata.get("ticker")).upper()] if chunk.metadata.get("ticker") else []
    )
    extracted = extract_entities(chunk.text, known_tickers=[*(known_tickers or ()), *doc_tickers])
    entities = extracted.flatten()
    present = {entity.upper() for entity in entities}
    for ticker in reversed(doc_tickers):
        if ticker not in present:
            entities.insert(0, ticker)
    chunk.entities = entities[:MAX_ENTITIES_PER_CHUNK]
    return chunk


# --------------------------------------------------------------------------
# Optional LLM-assisted pass (config-gated; engine injectable for tests)
# --------------------------------------------------------------------------

_LLM_PROMPT = """Extract financial entities from the text below. Reply with ONLY a \
JSON object using exactly these keys (arrays of strings, empty arrays allowed):
{{"tickers": [], "dates": [], "periods": [], "money": [], "percentages": [], "metrics": []}}
Dates in ISO format (YYYY-MM-DD) where possible; periods like FY2024/Q3 2024; \
metrics as "metric: value".

Text:
{text}"""


def extract_entities_llm(
    text: str, engine: Any, *, known_tickers: Iterable[str] | None = None
) -> ExtractedEntities:
    """LLM-assisted extraction merged onto the deterministic baseline.

    The regex result is the floor: LLM additions are validated and appended,
    and any failure (unavailable provider, malformed JSON, wrong shape) simply
    returns the deterministic result. Never raises.
    """
    baseline = extract_entities(text, known_tickers=known_tickers)
    if not text.strip():
        return baseline
    try:
        response = engine.generate(_LLM_PROMPT.format(text=text[:8000]), json_mode=True)
        payload = parse_json_object(response.text)
        return merge_entities(baseline, payload)
    except Exception:  # noqa: BLE001 — extraction is best-effort by contract
        return baseline


_VALID_KEYS = {"tickers", "dates", "periods", "money", "percentages", "metrics"}
_KEY_TO_FIELD = {
    "tickers": "tickers",
    "dates": "dates",
    "periods": "periods",
    "money": "money",
    "percentages": "percentages",
    "metrics": "metrics",
}


def parse_json_object(text: str) -> dict:
    """Pull the first JSON object out of an LLM reply (tolerates code fences
    and surrounding prose). Raises ValueError when nothing parses."""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found in model reply") from None
        payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("model reply JSON is not an object")
    return payload


def merge_entities(baseline: ExtractedEntities, payload: dict) -> ExtractedEntities:
    """Union LLM payload into the baseline; unknown keys and non-string entries
    are dropped silently — the deterministic pass defines correctness."""
    merged = ExtractedEntities(
        tickers=list(baseline.tickers),
        dates=list(baseline.dates),
        periods=list(baseline.periods),
        money=list(baseline.money),
        percentages=list(baseline.percentages),
        metrics=list(baseline.metrics),
    )
    for key, attr in _KEY_TO_FIELD.items():
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        current = getattr(merged, attr)
        for value in values:
            if isinstance(value, str) and value.strip():
                current.append(value.strip())
    merged.tickers = [t.upper() for t in _dedupe(merged.tickers)]
    merged.dates = _dedupe(merged.dates)
    merged.periods = _dedupe(p.upper() for p in merged.periods)
    merged.money = _dedupe(merged.money)
    merged.percentages = _dedupe(merged.percentages)
    merged.metrics = _dedupe(merged.metrics)
    return merged


__all__ = [
    "BUILTIN_TICKERS",
    "ExtractedEntities",
    "MAX_ENTITIES_PER_CHUNK",
    "enrich_chunk",
    "extract_entities",
    "extract_entities_llm",
    "merge_entities",
    "parse_json_object",
]
