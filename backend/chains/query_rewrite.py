"""Query rewriting / normalization (spec section 10.4 — kept cheap).

Deterministic FIRST: whitespace cleanup, polite-filler stripping, and ticker
normalization run with zero LLM dependency, producing a normalized query plus
a conservative metadata filter (ticker) when exactly one ticker is detected.

An optional LLM-assisted mode (settings.enable_llm_query_rewrite) asks the
engine for a JSON {"query": ...} rewrite; any failure falls back to the
deterministic result, and `mode` records which path produced the output.

Filter conservatism rule: comparison questions ("compare AAPL and MSFT")
detect TWO tickers and therefore emit NO ticker filter — filtering would
exclude half the evidence.
"""

import json
import logging
import re
from dataclasses import dataclass, field

from config.settings import Settings, get_settings
from llm_providers.base import GenerationResult

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Deterministic pass
# --------------------------------------------------------------------------

# Greetings and politeness wrappers only — never interrogatives ("what",
# "how"), which must survive into the retrieval query.
_FILLER_PATTERNS = [
    re.compile(r"^(hey|hi|hello)\s+(sentinel|there)[,\s]+", re.IGNORECASE),
    re.compile(
        r"^(?:please\s+)?(?:can|could|would)\s+you\s+(?:please\s+)?"
        r"(?:tell me|show me|explain|describe|look up|find)[,\s]+",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(i\s+(?:would|'d)\s+like\s+(?:to\s+know|to\s+see)|"
        r"i\s+(?:want|need)\s+to\s+(?:know|see))[,\s]+",
        re.IGNORECASE,
    ),
]

_DOLLAR_TICKER_RE = re.compile(r"\$([A-Za-z]{2,5})\b")
_TOKEN_RE = re.compile(r"\b([A-Z][A-Z0-9.&/-]{0,6})\b")

_LLM_REWRITE_PROMPT = """Rewrite the user's financial research question as a concise, \
self-contained retrieval query. Expand pronouns into entity names, keep tickers \
uppercase, drop pleasantries. Reply with ONLY a JSON object:
{{"query": "<rewritten question>"}}

Question:
{question}"""


@dataclass
class RewriteResult:
    original: str
    rewritten: str
    tickers: list[str] = field(default_factory=list)
    filters: dict = field(default_factory=dict)
    mode: str = "heuristic"  # "heuristic" | "llm"
    changed: bool = False


class QueryRewriter:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        engine=None,
        known_tickers: frozenset[str] | set[str] | None = None,
    ):
        self.settings = settings or get_settings()
        self.engine = engine  # only needed for the LLM-assisted mode
        from ingestion.entity_extractor import BUILTIN_TICKERS

        self.known_tickers = (
            frozenset(known_tickers) if known_tickers is not None else BUILTIN_TICKERS
        )

    def rewrite(self, question: str, history: list[dict] | None = None) -> RewriteResult:
        """Normalize a question. Never raises; never returns an empty query."""
        result = self._rewrite_heuristic(question, history=history)
        if self.settings.enable_llm_query_rewrite and self.engine is not None:
            try:
                return self._rewrite_llm(question, result)
            except Exception as exc:  # noqa: BLE001 — heuristic output is the floor
                logger.info("LLM rewrite failed (%s); using deterministic result", exc)
        return result

    # -- deterministic ----------------------------------------------------------

    def _rewrite_heuristic(
        self, question: str, history: list[dict] | None = None
    ) -> RewriteResult:
        cleaned = re.sub(r"\s+", " ", question).strip()

        filler_removed = True
        while filler_removed:
            filler_removed = False
            for pattern in _FILLER_PATTERNS:
                stripped = pattern.sub("", cleaned, count=1).strip()
                if stripped != cleaned:
                    cleaned = stripped
                    filler_removed = True

        tickers = self._detect_tickers(cleaned)

        # Context-aware follow-up resolution:
        if history:
            prev_user_queries = [
                turn.get("content", "")
                if isinstance(turn, dict)
                else getattr(turn, "content", "")
                for turn in history
                if (
                    turn.get("role") if isinstance(turn, dict) else getattr(turn, "role", "")
                )
                == "user"
            ]
            last_q = prev_user_queries[-1].strip() if prev_user_queries else ""
            cleaned_lower = cleaned.lower()
            action_commands = {
                "do it",
                "do that",
                "do",
                "go ahead",
                "summarize",
                "summarize it",
                "expand",
                "expand it",
                "tell me",
                "yes",
                "please do",
                "continue",
                "what are they",
                "list them",
                "explain",
                "give details",
                "elaborate",
            }
            if cleaned_lower in action_commands and last_q:
                cleaned = last_q
                tickers = self._detect_tickers(cleaned)
            elif not tickers:
                for turn in reversed(history):
                    content = (
                        turn.get("content", "")
                        if isinstance(turn, dict)
                        else getattr(turn, "content", "")
                    )
                    prev_tickers = self._detect_tickers(content)
                    if prev_tickers:
                        tickers = prev_tickers
                        words = cleaned_lower.split()
                        if len(words) <= 6 or any(
                            w in words
                            for w in (
                                "it",
                                "its",
                                "their",
                                "that",
                                "this",
                                "do",
                                "how",
                                "what",
                                "and",
                                "why",
                                "more",
                            )
                        ):
                            cleaned = f"{tickers[0]} {cleaned}"
                        break

        # Normalize cashtags to canonical uppercase so embeddings see one form.
        rewritten = _DOLLAR_TICKER_RE.sub(lambda m: f"${m.group(1).upper()}", cleaned)
        rewritten = rewritten if rewritten else question.strip()
        filters: dict = {}
        if len(tickers) == 1:
            filters["ticker"] = tickers[0]

        return RewriteResult(
            original=question,
            rewritten=rewritten,
            tickers=tickers,
            filters=filters,
            mode="heuristic",
            changed=rewritten != question.strip(),
        )

    def _detect_tickers(self, text: str) -> list[str]:
        from ingestion.entity_extractor import COMPANY_ALIASES

        found: list[str] = []
        for match in _DOLLAR_TICKER_RE.finditer(text):
            found.append(match.group(1).upper())
        lower_text = text.lower()
        for company_name, ticker in COMPANY_ALIASES.items():
            if re.search(rf"\b{re.escape(company_name)}\b", lower_text):
                found.append(ticker)
        for match in _TOKEN_RE.finditer(text):
            token = match.group(1).rstrip(".&-/")
            if token in self.known_tickers:
                found.append(token)
        ordered: list[str] = []
        for ticker in found:
            if ticker not in ordered:
                ordered.append(ticker)
        return ordered

    # -- LLM-assisted -------------------------------------------------------------

    def _rewrite_llm(self, question: str, fallback: RewriteResult) -> RewriteResult:
        assert self.engine is not None
        response: GenerationResult = self.engine.generate(
            _LLM_REWRITE_PROMPT.format(question=question), json_mode=True, temperature=0.0
        )
        payload = json.loads(_extract_json(response.text))
        rewritten = payload.get("query")
        if not isinstance(rewritten, str) or not rewritten.strip():
            raise ValueError("LLM rewrite returned no usable query")
        merged_tickers = list(dict.fromkeys(fallback.tickers + self._detect_tickers(rewritten)))
        filters = {"ticker": merged_tickers[0]} if len(merged_tickers) == 1 else {}
        return RewriteResult(
            original=question,
            rewritten=re.sub(r"\s+", " ", rewritten).strip(),
            tickers=merged_tickers,
            filters=filters,
            mode="llm",
            changed=rewritten.strip() != question.strip(),
        )


def _extract_json(text: str) -> str:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in rewrite reply")
    return cleaned[start : end + 1]


__all__ = ["QueryRewriter", "RewriteResult"]
