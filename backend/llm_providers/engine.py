"""LLMEngine — fallback chain over BaseProviders (spec section 9).

Provider order comes from settings.llm_provider_order (e.g. "openai,ollama").
For every call the engine walks that order:

1. Skip providers that aren't available right now (availability cached for
   settings.availability_ttl_seconds so Ollama's loopback probe doesn't run on
   every request).
2. Attempt the call with bounded retries: transient failures and rate limits
   retry up to settings.llm_max_retries times with exponential backoff,
   honoring a provider-supplied Retry-After when present.
3. Error routing:
   - InvalidRequestError  -> abort immediately (the payload is broken; other
     providers would reject it too)
   - AuthenticationError  -> no same-provider retry (credentials won't heal),
     fall through to the next provider
   - transient / rate-limit after final retry -> fall through to next provider

Each provider call becomes its own trace named llm.generate / llm.embed with a
span per attempt carrying provider, model, attempt number, usage, cost, and
error metadata. Successful results carry provider name, model, token usage,
measured latency, and estimated cost.

`sleeper` and `jitter` are injectable so tests observe backoff deterministically
without real delays. No network happens at construction time.
"""

import logging
import random
import time
from collections.abc import Callable
from typing import Any

from config.settings import Settings, get_settings
from llm_providers.base import (
    AuthenticationError,
    BaseProvider,
    EmbeddingResult,
    GenerationResult,
    InvalidRequestError,
    ProviderUnavailableError,
    RateLimitError,
    TransientProviderError,
    estimate_cost_usd,
    measure_latency_ms,
)
from observability.langfuse_wrapper import NULL_TRACER, Trace, Tracer

logger = logging.getLogger(__name__)


def default_providers(settings: Settings) -> list[BaseProvider]:
    """Instantiate the configured provider chain. Always safe: providers whose
    package/key is missing simply report is_available() == False."""
    # Imported inside this factory so importing llm_providers.engine never
    # pulls optional SDK modules.
    from llm_providers.ollama_provider import OllamaProvider
    from llm_providers.openai_provider import OpenAIProvider

    factories: dict[str, Callable[[], BaseProvider]] = {
        "openai": lambda: OpenAIProvider(settings=settings),
        "ollama": lambda: OllamaProvider(settings=settings),
    }
    order = [n.strip().lower() for n in settings.llm_provider_order.split(",") if n.strip()]
    return [factories[name]() for name in order if name in factories]


class LLMEngine:
    """Routes generate()/embed() calls down the configured provider chain."""

    def __init__(
        self,
        providers: list[BaseProvider] | None = None,
        *,
        settings: Settings | None = None,
        tracer: Tracer | None = None,
        sleeper: Callable[[float], None] | None = None,
        jitter: Callable[[int], float] | None = None,
    ):
        self.settings = settings or get_settings()
        self.providers = providers if providers is not None else default_providers(self.settings)
        self.tracer = tracer if tracer is not None else NULL_TRACER
        self._sleeper = sleeper if sleeper is not None else time.sleep
        self._jitter = jitter if jitter is not None else (lambda attempt: random.uniform(0, 0.25))
        self._availability: dict[str, tuple[float, bool]] = {}

    # -- availability ---------------------------------------------------------

    def _is_available(self, provider: BaseProvider, refresh: bool = False) -> bool:
        now = time.monotonic()
        if not refresh:
            cached = self._availability.get(provider.name)
            if cached and now - cached[0] < self.settings.availability_ttl_seconds:
                return cached[1]
        try:
            ok = bool(provider.is_available())
        except Exception:  # noqa: BLE001 — a failing check means "not usable"
            logger.warning("Availability probe for %s raised", provider.name, exc_info=True)
            ok = False
        self._availability[provider.name] = (now, ok)
        return ok

    def available_providers(self, refresh: bool = False) -> list[str]:
        """Names of currently-available providers, in configured order."""
        return [p.name for p in self.providers if self._is_available(p, refresh=refresh)]

    def has_generation(self, refresh: bool = False) -> bool:
        return bool(self.available_providers(refresh=refresh))

    def has_embedding(self, refresh: bool = False) -> bool:
        return any(
            p.supports_embeddings and self._is_available(p, refresh=refresh) for p in self.providers
        )

    def invalidate_availability(self) -> None:
        self._availability.clear()

    # -- public operations ------------------------------------------------------

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> GenerationResult:
        def call(provider: BaseProvider) -> GenerationResult:
            return provider.generate(
                prompt,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
            )

        return self._run_chain("generate", call)

    def embed(self, texts: list[str]) -> list[EmbeddingResult]:
        if not texts:
            return []

        def call(provider: BaseProvider) -> list[EmbeddingResult]:
            return provider.embed(texts)

        return self._run_chain("embed", call)

    # -- chain mechanics ----------------------------------------------------------

    def _run_chain(self, operation: str, call: Callable[[BaseProvider], Any]) -> Any:
        needs_embeddings = operation == "embed"
        candidates: list[BaseProvider] = []
        for provider in self.providers:
            if needs_embeddings and not provider.supports_embeddings:
                continue
            if self._is_available(provider):
                candidates.append(provider)

        last_error: Exception | None = None
        for index, provider in enumerate(candidates):
            try:
                return self._attempt_with_retries(operation, provider, index, call)
            except InvalidRequestError:
                # Same payload would be rejected everywhere — surface it now.
                raise
            except (AuthenticationError, TransientProviderError) as exc:
                last_error = exc
                logger.info(
                    "%s via %s failed (%s); %s",
                    operation,
                    provider.name,
                    exc,
                    "no more providers" if index == len(candidates) - 1 else "falling back",
                )
        if last_error is not None:
            raise ProviderUnavailableError(
                f"All configured LLM providers failed for {operation}; last error: {last_error}"
            ) from last_error
        raise ProviderUnavailableError(
            f"No LLM provider available for {operation} "
            f"(configured: {[p.name for p in self.providers]})"
        )

    def _attempt_with_retries(
        self,
        operation: str,
        provider: BaseProvider,
        provider_index: int,
        call: Callable[[BaseProvider], Any],
    ) -> Any:
        max_attempts = max(self.settings.llm_max_retries + 1, 1)
        trace: Trace = self.tracer.start_trace(
            f"llm.{operation}", input={"provider": provider.name}
        )
        for attempt in range(max_attempts):
            span = trace.span(
                f"{operation}.attempt",
                provider=provider.name,
                attempt=attempt + 1,
                provider_index=provider_index,
            )
            started = time.perf_counter()
            try:
                result = call(provider)
            except InvalidRequestError as exc:
                span.record_error(_error_label(exc))
                span.finish()
                trace.finish(output={"status": "invalid_request"})
                raise
            except AuthenticationError as exc:
                span.record_error(_error_label(exc))
                span.finish()
                trace.finish(output={"status": "authentication_failed"})
                raise  # engine decides fall-through
            except RateLimitError as exc:
                span.record_error(_error_label(exc))
                if attempt == max_attempts - 1:
                    span.finish()
                    trace.finish(output={"status": "rate_limited_exhausted"})
                    raise
                delay = (
                    exc.retry_after if exc.retry_after is not None else self._backoff_delay(attempt)
                )
                span.metadata["retry_delay_s"] = round(delay, 3)
                span.finish()
                logger.info(
                    "Rate limited by %s; retrying in %.2fs (attempt %d/%d)",
                    provider.name,
                    delay,
                    attempt + 1,
                    max_attempts,
                )
                self._sleeper(delay)
                continue
            except TransientProviderError as exc:
                span.record_error(_error_label(exc))
                if attempt == max_attempts - 1:
                    span.finish()
                    trace.finish(output={"status": "transient_exhausted"})
                    raise
                delay = self._backoff_delay(attempt)
                span.metadata["retry_delay_s"] = round(delay, 3)
                span.finish()
                logger.info(
                    "Transient failure from %s (%s); retrying in %.2fs (attempt %d/%d)",
                    provider.name,
                    exc,
                    delay,
                    attempt + 1,
                    max_attempts,
                )
                self._sleeper(delay)
                continue

            # Success: enrich observability fields, close the trace.
            latency_ms = measure_latency_ms(started)
            _annotate_result(result, provider.name, latency_ms)
            meta = _result_metadata(result)
            span.metadata.update(meta)
            span.finish()
            trace.finish(output=meta)
            return result
        raise AssertionError("unreachable")  # pragma: no cover

    def _backoff_delay(self, attempt: int) -> float:
        return self.settings.llm_backoff_base_seconds * (2**attempt) + self._jitter(attempt)


# -- helpers --------------------------------------------------------------------


def _error_label(exc: Exception) -> str:
    """Class name + message so traces distinguish error kinds."""
    return f"{type(exc).__name__}: {exc}"


def _annotate_result(result: Any, provider_name: str, latency_ms: float) -> None:
    """Fill provider/latency/cost on typed results when the provider didn't."""
    items = result if isinstance(result, list) else [result]
    for item in items:
        if not isinstance(item, (GenerationResult, EmbeddingResult)):
            continue
        if not item.provider:
            item.provider = provider_name
        if item.latency_ms == 0.0:
            item.latency_ms = latency_ms
        if item.cost_usd is None:
            item.cost_usd = estimate_cost_usd(item.model, item.usage)


def _result_metadata(result: Any) -> dict[str, Any]:
    if isinstance(result, GenerationResult):
        return {
            "model": result.model,
            "latency_ms": round(result.latency_ms, 2),
            "usage_total_tokens": result.usage.total_tokens if result.usage else None,
            "cost_usd": result.cost_usd,
            "finish_reason": result.finish_reason,
            "output_chars": len(result.text),
        }
    if isinstance(result, EmbeddingResult):
        return {"model": result.model, "dims": len(result.vector)}
    if isinstance(result, list):  # embed batch
        return {
            "vectors": len(result),
            "dims": len(result[0].vector) if result else 0,
        }
    return {}
