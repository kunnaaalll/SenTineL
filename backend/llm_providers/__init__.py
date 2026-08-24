"""LLM provider layer (spec section 9): BaseProvider contract, OpenAI +
Ollama implementations, and the fallback-chain LLMEngine. Importing this
package never requires provider SDKs or API keys."""

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
)
from llm_providers.engine import LLMEngine, default_providers

__all__ = [
    "AuthenticationError",
    "BaseProvider",
    "EmbeddingResult",
    "GenerationResult",
    "InvalidRequestError",
    "LLMEngine",
    "ProviderError",
    "ProviderUnavailableError",
    "RateLimitError",
    "TokenUsage",
    "TransientProviderError",
    "default_providers",
    "estimate_cost_usd",
]
