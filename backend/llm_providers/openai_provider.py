"""OpenAI provider (spec section 9) — primary generation + embeddings backend.

Import safety: the openai package is imported inside a guard; a missing
package or missing OPENAI_API_KEY degrades to is_available() == False and
typed ProviderUnavailableError on use — never an ImportError and never a
network call at import/construction time (the client is created lazily; the
OpenAI constructor performs no I/O).

SDK exceptions are classified into the ProviderError taxonomy by duck-typed
inspection (status_code / class name) so the classification works identically
against the real SDK and against offline fakes.
"""

import logging
import time
from typing import Any

from config.settings import Settings, get_settings, resolve_secret
from llm_providers.base import (
    AuthenticationError,
    BaseProvider,
    EmbeddingResult,
    GenerationResult,
    InvalidRequestError,
    ProviderError,
    ProviderUnavailableError,
    RateLimitError,
    TokenUsage,
    TransientProviderError,
    estimate_cost_usd,
    measure_latency_ms,
)

logger = logging.getLogger(__name__)

try:  # optional at runtime — absence degrades, never crashes
    import openai as _openai
except ImportError:  # pragma: no cover - exercised via monkeypatched None in tests
    _openai = None  # type: ignore[assignment]

_NON_RETRYABLE_STATUSES = {400, 401, 403, 404, 405, 413, 422}


def _response_status(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def _retry_after_seconds(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    raw = headers.get("retry-after") if hasattr(headers, "get") else None
    if raw is None:
        return None
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        return None


def classify_sdk_exception(exc: BaseException) -> ProviderError:
    """Map any SDK/client exception onto the ProviderError taxonomy.

    Duck-typed on purpose: works for real openai SDK errors (which carry
    .response.status_code) and for offline fakes alike.
    """
    name = type(exc).__name__
    status = _response_status(exc)

    if status == 401 or "Authentication" in name or "PermissionDenied" in name:
        return AuthenticationError(f"{name}: credentials rejected")
    if status == 429 or "RateLimit" in name:
        return RateLimitError(f"{name}: rate limited", retry_after=_retry_after_seconds(exc))
    if status is not None and status in _NON_RETRYABLE_STATUSES:
        return InvalidRequestError(f"{name}: request rejected (HTTP {status})")
    if "Timeout" in name or "Connection" in name or "APITimeout" in name:
        return TransientProviderError(f"{name}: transient network failure")
    if status is not None and status >= 500:
        return TransientProviderError(f"{name}: provider server error (HTTP {status})")
    if "NotFound" in name:
        return InvalidRequestError(f"{name}: unknown model or resource")
    # Unknown SDK error shape: treat as transient (retryable, then fallback).
    return TransientProviderError(f"{name}: unrecognized provider failure")


def _usage_from(payload: Any) -> TokenUsage | None:
    if payload is None:
        return None
    prompt = int(getattr(payload, "prompt_tokens", 0) or 0)
    completion = int(getattr(payload, "completion_tokens", 0) or 0)
    total = int(getattr(payload, "total_tokens", 0) or (prompt + completion))
    return TokenUsage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)


class OpenAIProvider(BaseProvider):
    name = "openai"
    supports_embeddings = True

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        generation_model: str | None = None,
        embedding_model: str | None = None,
        timeout_seconds: float | None = None,
        settings: Settings | None = None,
    ):
        self.settings = settings or get_settings()
        # Resolve SecretStr once; everything downstream sees plain str | None
        # exactly as before (availability booleans, SDK hand-off).
        self.api_key = (
            api_key if api_key is not None else resolve_secret(self.settings.openai_api_key)
        )
        self.base_url = base_url if base_url is not None else self.settings.openai_base_url
        default_gen_model = self.settings.openai_generation_model
        if self.base_url and "groq.com" in self.base_url.lower() and default_gen_model in ("gpt-4o-mini", "gpt-4o", "gpt-4"):
            default_gen_model = "llama-3.3-70b-versatile"
        elif self.base_url and "x.ai" in self.base_url.lower() and default_gen_model in ("gpt-4o-mini", "gpt-4o", "gpt-4"):
            default_gen_model = "grok-2-latest"

        self.generation_model = generation_model or default_gen_model
        self.embedding_model = embedding_model or self.settings.openai_embedding_model
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else self.settings.llm_timeout_seconds
        )
        self._client: Any | None = None
        logger.info(
            "OpenAIProvider init: base_url=%s, generation_model=%s (settings=%s)",
            self.base_url,
            self.generation_model,
            self.settings.openai_generation_model,
        )

    # -- availability / client ------------------------------------------------

    def is_available(self) -> bool:
        """Package installed + key present. Static checks only — auth problems
        surface at call time and route the engine to the next provider."""
        return _openai is not None and bool(self.api_key)

    @property
    def client(self) -> Any:
        if self._client is None:
            if _openai is None:
                raise ProviderUnavailableError(
                    "openai package is not installed; pip install openai to use this provider"
                )
            if not self.api_key:
                raise ProviderUnavailableError("OPENAI_API_KEY is not set")
            client_kwargs: dict[str, Any] = {
                "api_key": self.api_key,
                "timeout": self.timeout_seconds,
                "max_retries": 0,
            }
            if self.base_url:
                client_kwargs["base_url"] = self.base_url
            self._client = _openai.OpenAI(**client_kwargs)
        return self._client

    # -- BaseProvider surface ---------------------------------------------------

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> GenerationResult:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict[str, Any] = {
            "model": self.generation_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 — re-raised as typed ProviderError
            classified = classify_sdk_exception(exc)
            # Enrich with model/base_url for debugging, but preserve original exception type and attrs
            classified.args = (
                f"{classified} (model={self.generation_model}, base_url={self.base_url})",
            )
            raise classified from exc

        choice = response.choices[0] if getattr(response, "choices", None) else None
        text = getattr(choice.message, "content", None) if choice else None
        usage = _usage_from(getattr(response, "usage", None))
        return GenerationResult(
            text=text or "",
            provider=self.name,
            model=self.generation_model,
            usage=usage,
            latency_ms=measure_latency_ms(started),
            cost_usd=estimate_cost_usd(self.generation_model, usage),
            finish_reason=getattr(choice, "finish_reason", None),
        )

    def embed(self, texts: list[str]) -> list[EmbeddingResult]:
        if not texts:
            return []
        started = time.perf_counter()

        # If base_url points to a third-party non-embedding endpoint (like Groq or xAI),
        # use Pinecone hosted inference or deterministic embedding fallback.
        is_non_embedding_host = bool(
            self.base_url and any(domain in self.base_url.lower() for domain in ("groq.com", "x.ai"))
        )

        if not is_non_embedding_host:
            try:
                response = self.client.embeddings.create(model=self.embedding_model, input=texts)
                usage = _usage_from(getattr(response, "usage", None))
                cost = estimate_cost_usd(self.embedding_model, usage)
                latency = measure_latency_ms(started)
                data = sorted(
                    getattr(response, "data", []), key=lambda item: getattr(item, "index", 0)
                )
                return [
                    EmbeddingResult(
                        vector=list(getattr(item, "embedding", []) or []),
                        text_index=index,
                        provider=self.name,
                        model=self.embedding_model,
                        usage=usage if index == 0 else None,
                        latency_ms=latency if index == 0 else 0.0,
                        cost_usd=cost if index == 0 else None,
                    )
                    for index, item in enumerate(data)
                ]
            except Exception as exc:  # noqa: BLE001
                if not self.base_url:
                    raise classify_sdk_exception(exc) from exc
                logger.warning("Embeddings call to %s failed: %s; falling back", self.base_url, exc)

        # Fallback 1: Pinecone Inference
        pinecone_key = resolve_secret(self.settings.pinecone_api_key)
        if pinecone_key:
            try:
                from pinecone import Pinecone

                pc = Pinecone(api_key=pinecone_key)
                res = pc.inference.embed(
                    model="multilingual-e5-large",
                    inputs=texts,
                    parameters={"input_type": "passage", "truncate": "END"},
                )
                latency = measure_latency_ms(started)
                dim = self.settings.embedding_dimension
                results = []
                data = getattr(res, "data", []) or []
                for index, item in enumerate(data):
                    vec = list(getattr(item, "values", []) or [])
                    if len(vec) < dim:
                        vec = vec + [0.0] * (dim - len(vec))
                    elif len(vec) > dim:
                        vec = vec[:dim]
                    results.append(
                        EmbeddingResult(
                            vector=vec,
                            text_index=index,
                            provider="pinecone-inference",
                            model="multilingual-e5-large",
                            usage=TokenUsage(
                                prompt_tokens=len(texts[index].split()),
                                total_tokens=len(texts[index].split()),
                            ),
                            latency_ms=latency if index == 0 else 0.0,
                            cost_usd=0.0,
                        )
                    )
                if len(results) == len(texts):
                    return results
            except Exception as pc_exc:
                logger.warning("Pinecone inference embedding failed: %s; using local vector", pc_exc)

        # Fallback 2: Deterministic 1536-dimensional unit vector
        import hashlib
        import math

        dim = self.settings.embedding_dimension
        results = []
        latency = measure_latency_ms(started)
        for index, text in enumerate(texts):
            vec = [0.0] * dim
            words = text.lower().split() or [text.lower()]
            for w_idx, word in enumerate(words):
                h = int(hashlib.sha256(f"{word}:{w_idx % 64}".encode()).hexdigest()[:8], 16)
                pos = h % dim
                val = ((h >> 8) % 1000) / 500.0 - 1.0
                vec[pos] += val
            norm = math.sqrt(sum(x * x for x in vec))
            vec = [x / norm for x in vec] if norm > 0 else [1.0] + [0.0] * (dim - 1)
            results.append(
                EmbeddingResult(
                    vector=vec,
                    text_index=index,
                    provider="deterministic-fallback",
                    model="pseudo-dense",
                    usage=TokenUsage(
                        prompt_tokens=len(words),
                        total_tokens=len(words),
                    ),
                    latency_ms=latency if index == 0 else 0.0,
                    cost_usd=0.0,
                )
            )
        return results
