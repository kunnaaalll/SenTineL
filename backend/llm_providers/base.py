"""LLM provider abstraction (spec section 9).

BaseProvider is the contract every backend implements; LLMEngine
(llm_providers.engine) drives a configured fallback chain of them.

Design rules:

- Typed results: generate()/embed() return GenerationResult/EmbeddingResult
  carrying model, token usage, latency, and estimated cost — never bare strings.
- Errors are classified into ProviderError subclasses so the engine can decide
  between retry (transient / rate limit), fall back to the next provider
  (authentication, exhausted retries), or abort outright (invalid request —
  the same malformed call would fail everywhere).
- Availability checks must be cheap and side-effect free where possible;
  they may touch the network only for genuinely local services (Ollama) and
  never at import time.
- No SDK import happens at module import: providers guard optional imports so
  the whole backend imports cleanly with no API keys and no provider packages.
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

# --------------------------------------------------------------------------
# Error taxonomy
# --------------------------------------------------------------------------


class ProviderError(Exception):
    """Base class for all LLM provider failures."""


class TransientProviderError(ProviderError):
    """Temporary failure — timeout, connection drop, 5xx. Safe to retry."""


class RateLimitError(TransientProviderError):
    """Provider asked us to slow down. Honors `retry_after` seconds when known."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class AuthenticationError(ProviderError):
    """Credentials missing/rejected. Never retried; engine falls to next provider."""


class InvalidRequestError(ProviderError):
    """Malformed request (bad parameter, unknown model). Never retried — the
    engine aborts the whole chain since the fault is in the caller's payload,
    not the provider."""


class ProviderUnavailableError(ProviderError):
    """No usable provider at all (package missing, key absent, service down)."""


# --------------------------------------------------------------------------
# Typed results
# --------------------------------------------------------------------------


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class GenerationResult:
    """One completion, with full observability metadata attached."""

    text: str
    provider: str = ""
    model: str = ""
    usage: TokenUsage | None = None
    latency_ms: float = 0.0
    cost_usd: float | None = None
    finish_reason: str | None = None


@dataclass
class EmbeddingResult:
    """One embedded input; embed() returns one per input text, in order."""

    vector: list[float]
    text_index: int = 0
    provider: str = ""
    model: str = ""
    usage: TokenUsage | None = None
    latency_ms: float = 0.0
    cost_usd: float | None = None


# --------------------------------------------------------------------------
# Cost estimation
# --------------------------------------------------------------------------

# Public list prices per 1M tokens as (input, output); matched by longest model
# name prefix so "text-embedding-3-small-512" style suffixes still resolve.
# Unknown models yield cost_usd=None rather than a made-up number.
COST_PER_1M_TOKENS: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "text-embedding-3-large": (0.13, 0.0),
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-ada-002": (0.10, 0.0),
}


def price_for_model(model: str) -> tuple[float, float] | None:
    """Longest-prefix lookup in COST_PER_1M_TOKENS; None when unlisted."""
    best: tuple[float, float] | None = None
    best_len = -1
    for prefix, prices in COST_PER_1M_TOKENS.items():
        if model.startswith(prefix) and len(prefix) > best_len:
            best, best_len = prices, len(prefix)
    return best


def estimate_cost_usd(model: str, usage: TokenUsage | None) -> float | None:
    """Estimated spend for one call, or None when usage/pricing is unknown."""
    if usage is None or usage.total_tokens <= 0:
        return None
    prices = price_for_model(model)
    if prices is None:
        return None
    input_price, output_price = prices
    return (
        usage.prompt_tokens * input_price / 1_000_000
        + usage.completion_tokens * output_price / 1_000_000
    )


def measure_latency_ms(start_perf: float) -> float:
    return (time.perf_counter() - start_perf) * 1000.0


# --------------------------------------------------------------------------
# Provider contract
# --------------------------------------------------------------------------


class BaseProvider(ABC):
    """One LLM backend (OpenAI cloud, local Ollama, test fakes)."""

    name: str = "base"
    # Embeddings capability can depend on configuration (Ollama needs an
    # explicit embedding model), so it's an instance property, not a class flag.
    supports_embeddings: bool = False

    @abstractmethod
    def is_available(self) -> bool:
        """True if this provider can serve requests right now.

        Must never raise. Cheap by design: static checks where possible, at
        worst a short-timeout probe of a local service."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> GenerationResult:
        """Complete `prompt` (with optional system preamble).

        Raises typed ProviderError subclasses on failure; json_mode asks the
        backend for a JSON-constrained response when it supports one."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[EmbeddingResult]:
        """Embed texts, one EmbeddingResult per input, order preserved."""
