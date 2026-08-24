"""Environment-driven configuration (spec sections 6.5, 8.2, 14.2).

Values load from real environment variables first, then a repo-root .env file.
adapters.yaml (which data sources are enabled) is parsed separately via
load_adapters_config(), with ${VAR} references expanded from the environment.
"""

import os
import re
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM providers (Phase 2)
    openai_api_key: str | None = None
    openai_generation_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_generation_model: str = "llama3.1"
    # None disables Ollama embeddings entirely (spec section 9: chat-only fallback)
    ollama_embedding_model: str | None = None
    # Comma-separated fallback order, e.g. "openai,ollama"
    llm_provider_order: str = "openai,ollama"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2  # retries per provider after the first attempt
    llm_backoff_base_seconds: float = 0.5  # exponential backoff base: base * 2**attempt
    availability_ttl_seconds: float = 30.0  # engine caches is_available() results

    # Ingestion pipeline (Phase 2)
    ingest_batch_size: int = 64
    delete_before_reingest: bool = True
    # Pinecone rejects per-vector metadata over ~40KB; stay under it with headroom
    pinecone_metadata_cap_bytes: int = 38_000
    enable_llm_entity_extraction: bool = False

    # Query path (Phase 2)
    enable_llm_query_rewrite: bool = False
    rag_top_k: int = 6
    rag_excerpt_chars: int = 1600
    rag_context_char_budget: int = 9000
    api_max_question_chars: int = 4000

    # Vector store
    pinecone_api_key: str | None = None
    pinecone_environment: str | None = None  # provider-side env/region label (legacy naming)
    pinecone_index_name: str = "sentinel"
    embedding_dimension: int = 1536  # text-embedding-3-small (spec 8.2)
    pinecone_cloud: str = "aws"  # used only when provisioning via ensure_index()
    pinecone_region: str = "us-east-1"

    # News API (Phase 3)
    news_api_key: str | None = None
    news_api_provider: str = "financial_modeling_prep"

    # Langfuse (Phase 4)
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    # APEX adapter (Phase 6; disabled in adapters.yaml by default)
    apex_endpoint_url: str | None = None

    # Runtime environment -> Pinecone namespace for dev/prod isolation (spec 8.2)
    sentinel_env: str = "dev"

    # SEC requires a descriptive User-Agent with contact info (spec 6.2)
    sec_contact_email: str = "sentinel-operator@example.com"

    @property
    def sec_user_agent(self) -> str:
        return f"Sentinel financial-research-copilot ({self.sec_contact_email})"

    @property
    def namespace(self) -> str:
        return self.sentinel_env


@lru_cache
def get_settings() -> Settings:
    return Settings()


_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def load_adapters_config(path: Path | None = None) -> dict:
    """Parse config/adapters.yaml, expanding ${VAR} references from the environment."""
    path = path or Path(__file__).resolve().parent / "adapters.yaml"
    raw = path.read_text(encoding="utf-8")
    expanded = _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), raw)
    return yaml.safe_load(expanded) or {}


def enabled_adapters(config: dict | None = None) -> list[str]:
    """Names of adapters whose `enabled` flag is true."""
    cfg = config if config is not None else load_adapters_config()
    return [name for name, conf in cfg.items() if isinstance(conf, dict) and conf.get("enabled")]
