"""Ollama provider (spec section 9) — free local fallback for dev/testing.

Talks to a local Ollama daemon over its REST API using plain `requests`
(no extra SDK, nothing to install). Chat is the default capability; embeddings
stay disabled until OLLAMA_EMBEDDING_MODEL is configured (spec: "chat only,
no embeddings by default").

Availability probing hits {base_url}/api/tags with a short timeout — it's a
loopback call by design, but tests inject a fake session so nothing ever
leaves the machine. All failures degrade to is_available() == False.
"""

import logging
import time
from typing import Any

import requests

from config.settings import Settings, get_settings
from llm_providers.base import (
    BaseProvider,
    EmbeddingResult,
    GenerationResult,
    InvalidRequestError,
    ProviderUnavailableError,
    RateLimitError,
    TokenUsage,
    TransientProviderError,
    estimate_cost_usd,
    measure_latency_ms,
)

logger = logging.getLogger(__name__)


def classify_response_status(status: int) -> None:
    """Raise the matching typed error for a non-2xx Ollama HTTP status."""
    if status == 429:
        raise RateLimitError("Ollama rate limited (HTTP 429)")
    if status in (400, 404):
        # 404 usually means "model not pulled" — a configuration fault.
        raise InvalidRequestError(f"Ollama rejected request (HTTP {status})")
    if status >= 500:
        raise TransientProviderError(f"Ollama server error (HTTP {status})")
    if status >= 400:
        raise InvalidRequestError(f"Ollama rejected request (HTTP {status})")


class OllamaProvider(BaseProvider):
    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        generation_model: str | None = None,
        embedding_model: str | None = None,
        timeout_seconds: float | None = None,
        settings: Settings | None = None,
        session: Any | None = None,
    ):
        self.settings = settings or get_settings()
        self.base_url = (base_url or self.settings.ollama_base_url).rstrip("/")
        self.generation_model = generation_model or self.settings.ollama_generation_model
        self.embedding_model = (
            embedding_model if embedding_model is not None else self.settings.ollama_embedding_model
        )
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else self.settings.llm_timeout_seconds
        )
        # Short probe timeout for is_available(); real calls use llm_timeout_seconds.
        self.probe_timeout_seconds = min(2.0, self.timeout_seconds)
        self.session = session or requests.Session()
        # Chat is the default capability; embeddings appear only once a model
        # is configured (spec section 9: "chat only, no embeddings by default").
        self.supports_embeddings = bool(self.embedding_model)

    # -- availability ---------------------------------------------------------

    def is_available(self) -> bool:
        """Daemon reachable? Loopback-only probe; any failure means unavailable."""
        try:
            response = self.session.get(
                f"{self.base_url}/api/tags", timeout=self.probe_timeout_seconds
            )
            status = getattr(response, "status_code", None)
            return status is not None and status < 400
        except Exception:  # noqa: BLE001 — unreachability IS the answer
            return False

    # -- generation -------------------------------------------------------------

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

        options: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        payload: dict[str, Any] = {
            "model": self.generation_model,
            "messages": messages,
            "stream": False,
            "options": options,
        }
        if json_mode:
            payload["format"] = "json"

        started = time.perf_counter()
        try:
            response = self.session.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise TransientProviderError(
                f"Ollama chat timed out after {self.timeout_seconds}s"
            ) from exc
        except requests.ConnectionError as exc:
            raise TransientProviderError("Ollama daemon unreachable") from exc

        if response.status_code >= 400:
            classify_response_status(response.status_code)

        try:
            body = response.json()
        except ValueError as exc:
            raise TransientProviderError("Ollama returned malformed JSON") from exc
        message = body.get("message") or {}
        usage = TokenUsage(
            prompt_tokens=int(body.get("prompt_eval_count", 0) or 0),
            completion_tokens=int(body.get("eval_count", 0) or 0),
            total_tokens=int(body.get("prompt_eval_count", 0) or 0)
            + int(body.get("eval_count", 0) or 0),
        )
        return GenerationResult(
            text=str(message.get("content", "") or ""),
            provider=self.name,
            model=str(body.get("model", self.generation_model)),
            usage=usage if usage.total_tokens else None,
            latency_ms=measure_latency_ms(started),
            cost_usd=None,  # local model — zero marginal cost, no price list entry
            finish_reason="stop" if body.get("done", True) else None,
        )

    # -- embeddings ---------------------------------------------------------------

    def embed(self, texts: list[str]) -> list[EmbeddingResult]:
        if not self.embedding_model:
            raise ProviderUnavailableError(
                "Ollama embeddings disabled (set OLLAMA_EMBEDDING_MODEL to enable)"
            )
        if not texts:
            return []
        payload = {"model": self.embedding_model, "input": texts}
        started = time.perf_counter()
        try:
            response = self.session.post(
                f"{self.base_url}/api/embed",
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise TransientProviderError(
                f"Ollama embed timed out after {self.timeout_seconds}s"
            ) from exc
        except requests.ConnectionError as exc:
            raise TransientProviderError("Ollama daemon unreachable") from exc

        if response.status_code >= 400:
            classify_response_status(response.status_code)
        try:
            body = response.json()
        except ValueError as exc:
            raise TransientProviderError("Ollama returned malformed JSON") from exc

        vectors = body.get("embeddings") or []
        if len(vectors) != len(texts):
            raise TransientProviderError(
                f"Ollama embed returned {len(vectors)} vectors for {len(texts)} inputs"
            )
        prompt_tokens = int(body.get("prompt_eval_count", 0) or 0)
        usage = (
            TokenUsage(prompt_tokens=prompt_tokens, total_tokens=prompt_tokens)
            if prompt_tokens
            else None
        )
        cost = estimate_cost_usd(self.embedding_model, usage)
        return [
            EmbeddingResult(
                vector=list(vector),
                text_index=index,
                provider=self.name,
                model=self.embedding_model,
                usage=usage if index == 0 else None,
                latency_ms=measure_latency_ms(started) if index == 0 else 0.0,
                cost_usd=cost if index == 0 else None,
            )
            for index, vector in enumerate(vectors)
        ]


__all__ = ["OllamaProvider", "classify_response_status"]
