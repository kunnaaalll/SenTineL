"""Extract agent (spec section 10.2) — structured facts with a safe floor.

Per retrieved chunk, one strict json_mode LLM call returns candidate facts;
each is validated into ExtractedFact (Pydantic, extra="forbid"). Rules the
model cannot break:

- Provenance: source_chunk_id is overwritten server-side with the chunk
  actually being processed — hallucinated provenance is impossible.
- Values are preserved verbatim (`value`); numeric_value is OUR conservative
  parse (suffix/scale aware), never the model's arithmetic.
- kind distinguishes reported / estimate / guidance / qualitative; unknown
  kinds degrade to qualitative rather than being trusted.
- Facts without entity + (value or statement) are rejected as malformed.

Failure handling:
- Per-chunk isolation: a malformed reply or provider error skips that chunk.
- Deterministic floor: if NO chunk yielded an LLM fact, regex extraction
  (ingestion.entity_extractor) produces low-confidence facts so downstream
  compare/synthesize still have grounded material. Partial LLM success keeps
  its higher-quality facts untouched.
"""

import logging
import re

from pydantic import ValidationError

from agents.state import VALID_CLAIM_KINDS, ExtractedFact
from config.settings import Settings, get_settings
from ingestion.entity_extractor import extract_entities, parse_json_object
from llm_providers.base import ProviderError
from models.schemas import RetrievedChunk
from observability.langfuse_wrapper import NULL_TRACER, Tracer

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = """You extract financial facts for a research system. \
From the excerpt, list every distinct fact as JSON objects inside \
{{"facts": [...]}} using EXACTLY these keys:

- "entity": company/ticker the fact is about (uppercase ticker when known)
- "metric": what is measured ("total net sales", "EPS", "debt to equity"); null if none
- "value": the EXACT figure string from the text ("$391,035", "12.5%"); null if qualitative
- "period": fiscal period or date the fact refers to ("FY2024", "Q3 2024"); null if unstated
- "kind": "reported" (actual disclosed figure), "estimate" (analyst/third-party estimate), \
"guidance" (company forward guidance), or "qualitative" (no number)
- "confidence": 0.0-1.0
- "statement": short verbatim quote supporting a qualitative claim; null otherwise

Rules: never invent numbers not present in the text; copy figures exactly \
including currency symbols and scale words; one fact per (entity, metric, period)."""

_EXTRACT_USER_TEMPLATE = """Excerpt ({chunk_id}):
{text}

JSON facts:"""

_TEXT_BUDGET_CHARS = 8000
_SCALE_FACTORS = {
    "thousand": 1_000.0,
    "million": 1_000_000.0,
    "billion": 1_000_000_000.0,
    "trillion": 1_000_000_000_000.0,
    "k": 1_000.0,
    "m": 1_000_000.0,
    "b": 1_000_000_000.0,
    "t": 1_000_000_000_000.0,
    "%": 1.0,
}
_NUMBER_RE = re.compile(r"^([\d][\d,]*(?:\.\d+)?)\s*([A-Za-z%]*)$")


def parse_numeric(value: str | None) -> float | None:
    """Conservative numeric parse of a reported value string.

    Handles $ signs, comma groupings, accounting negatives "(...)", percent,
    and explicit scale words/suffixes ("million"/"M"/"B"). Anything ambiguous
    returns None — we never guess scales."""
    if not value:
        return None
    text = value.strip()
    negative = text.startswith("(") and text.endswith(")")
    cleaned = text.strip("()%$ \t")
    match = _NUMBER_RE.match(cleaned)
    if not match:
        return None
    digits, suffix = match.groups()
    try:
        number = float(digits.replace(",", ""))
    except ValueError:
        return None
    factor = _SCALE_FACTORS.get(suffix.lower())
    if suffix and factor is None:
        return None
    return -number if negative else number * (factor or 1.0)


def unit_for(value: str | None) -> str | None:
    """Crude unit label consumed by the compare agent's comparability checks."""
    if not value:
        return None
    lowered = value.lower()
    if "%" in value:
        return "%"
    for word in ("trillion", "billion", "million", "thousand"):
        if word in lowered:
            return f"{word} USD" if "$" in value or "usd" in lowered else word
    if "$" in value:
        return "USD"
    if re.search(r"\d[\d,.]*\s*(k|m|b|t)\b", lowered):
        return "USD"
    return None


class ExtractAgent:
    name = "extract"

    def __init__(
        self,
        *,
        engine,
        settings: Settings | None = None,
        tracer: Tracer | None = None,
        max_chunks: int = 12,
        temperature: float = 0.0,
    ):
        self.engine = engine
        self.settings = settings or get_settings()
        self.tracer = tracer if tracer is not None else NULL_TRACER
        self.max_chunks = max_chunks
        self.temperature = temperature

    def __call__(self, state: dict) -> dict:
        trace = self.tracer.start_trace(
            "agent_extract",
            input={"chunks": len(state.get("retrieved_chunks") or [])},
        )
        selected = self._select_chunks(state.get("retrieved_chunks") or [])
        facts: list[ExtractedFact] = []
        errors: list[dict] = []
        for chunk in selected:
            extracted, error = self._extract_from_chunk(chunk)
            facts.extend(extracted)
            if error:
                errors.append(error)

        limitations: list[str] = []
        if not facts and selected:
            # Deterministic floor — nothing survived validation anywhere.
            logger.info("LLM extraction produced no facts; falling back to regex pass")
            limitations = ["structured extraction unavailable; used keyword-level facts"]
            for chunk in selected:
                facts.extend(deterministic_facts(chunk))

        trace.finish(output={"facts": len(facts), "errors": len(errors)})
        return {
            "extracted_facts": [fact.model_dump() for fact in _dedupe_facts(facts)],
            "agent_path": [*state.get("agent_path", []), self.name],
            "node_errors": [*state.get("node_errors", []), *errors],
            "trace_urls": [*state.get("trace_urls", []), trace.url],
            **({"limitations": limitations} if limitations else {}),
        }

    def _select_chunks(self, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        seen: set[str] = set()
        selected: list[RetrievedChunk] = []
        for chunk in sorted(chunks, key=lambda c: (-c.score, c.chunk_id)):
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            selected.append(chunk)
            if len(selected) >= self.max_chunks:
                break
        return selected

    def _extract_from_chunk(self, chunk: RetrievedChunk) -> tuple[list[ExtractedFact], dict | None]:
        prompt = _EXTRACT_USER_TEMPLATE.format(
            chunk_id=chunk.chunk_id, text=chunk.text[:_TEXT_BUDGET_CHARS]
        )
        try:
            response = self.engine.generate(
                prompt,
                system=EXTRACTION_SYSTEM_PROMPT,
                temperature=self.temperature,
                json_mode=True,
            )
        except ProviderError as exc:
            return [], {
                "node": self.name,
                "error": type(exc).__name__,
                "chunk": chunk.chunk_id,
            }
        try:
            payload = parse_json_object(response.text)
            raw_items = payload.get("facts")
            if not isinstance(raw_items, list):
                raise ValueError("'facts' is not a list")
        except ValueError as exc:
            return [], {
                "node": self.name,
                "error": f"malformed output: {exc}",
                "chunk": chunk.chunk_id,
            }
        except Exception as exc:  # noqa: BLE001 — any parse surprise is malformed output
            return [], {
                "node": self.name,
                "error": f"malformed output: {exc}",
                "chunk": chunk.chunk_id,
            }

        validated: list[ExtractedFact] = []
        for raw in raw_items:
            fact = self._validate_fact(raw, chunk)
            if fact is not None:
                validated.append(fact)
        return validated, None

    def _validate_fact(self, raw, chunk: RetrievedChunk) -> ExtractedFact | None:
        if not isinstance(raw, dict):
            return None
        entity = raw.get("entity")
        value = raw.get("value") if isinstance(raw.get("value"), str) else None
        statement = raw.get("statement") if isinstance(raw.get("statement"), str) else None
        if not isinstance(entity, str) or not entity.strip():
            return None
        if not value and not statement:
            return None  # nothing substantive to carry forward
        kind = raw.get("kind")
        if kind not in VALID_CLAIM_KINDS:
            kind = "qualitative"
        try:
            confidence = float(raw.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        metric = raw.get("metric")
        period = raw.get("period")
        try:
            return ExtractedFact(
                entity=entity.strip().upper(),
                metric=(
                    str(metric).strip().lower()
                    if isinstance(metric, str) and metric.strip()
                    else None
                ),
                value=value,
                numeric_value=parse_numeric(value),
                unit=unit_for(value),
                period=(
                    str(period).strip() if isinstance(period, str) and period.strip() else None
                ),
                kind=kind,
                confidence=max(0.0, min(confidence, 1.0)),
                statement=statement or None,
                source_chunk_id=chunk.chunk_id,  # forced provenance
            )
        except ValidationError:
            return None


def deterministic_facts(chunk: RetrievedChunk) -> list[ExtractedFact]:
    """Regex-based floor facts for one chunk (no LLM). Low confidence by design."""
    doc_ticker = str(chunk.metadata.get("ticker") or "").upper()
    entities = extract_entities(chunk.text)
    entity = doc_ticker or (entities.tickers[0] if entities.tickers else "UNKNOWN")

    facts: list[ExtractedFact] = []
    for entry in entities.metrics:
        metric, _, figure = entry.partition(": ")
        facts.append(
            ExtractedFact(
                entity=entity,
                metric=metric.strip(),
                value=figure.strip(),
                numeric_value=parse_numeric(figure),
                unit=unit_for(figure),
                kind="qualitative",
                confidence=0.3,
                source_chunk_id=chunk.chunk_id,
                statement=None,
            )
        )
    for figure in entities.money[:4]:
        facts.append(
            ExtractedFact(
                entity=entity,
                metric=None,
                value=figure,
                numeric_value=parse_numeric(figure),
                unit=unit_for(figure),
                kind="qualitative",
                confidence=0.2,
                source_chunk_id=chunk.chunk_id,
                statement=f"reports {figure}",
            )
        )
    return facts[:8]


def _dedupe_facts(facts: list[ExtractedFact]) -> list[ExtractedFact]:
    """Identical (entity, metric, value, period, chunk) duplicates collapse."""
    seen: set[tuple] = set()
    ordered: list[ExtractedFact] = []
    for fact in facts:
        key = (fact.entity, fact.metric, fact.value, fact.period, fact.source_chunk_id)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(fact)
    return ordered


__all__ = [
    "EXTRACTION_SYSTEM_PROMPT",
    "ExtractAgent",
    "deterministic_facts",
    "parse_numeric",
    "unit_for",
]
