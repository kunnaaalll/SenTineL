"""Environment-driven configuration (spec sections 6.5, 8.2, 14.2).

Values load from real environment variables first, then a repo-root .env file.
adapters.yaml (which data sources are enabled) is parsed separately via
load_adapters_config(), with ${VAR} references expanded from the environment.

Secret handling: every credential field is a pydantic ``SecretStr`` — repr(),
logging, or accidental dict-dumping of a Settings object shows only
``**********``. SDK hand-off sites resolve once via :func:`resolve_secret`
(None for unset/empty, plain text otherwise), so availability booleans behave
exactly as before while raw values never sit on shared objects.

Production gating (SENTINEL_ENV=prod): construction FAILS unless
``production_blockers()`` is empty — a real SEC contact email plus the two
credentials the service fundamentally cannot serve queries without (LLM +
vector store). Optional providers (news, Langfuse, APEX, Ollama) stay
optional in every environment and degrade gracefully.
"""

import logging
import os
import re
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]

logger = logging.getLogger(__name__)

# Domains reserved by RFC 2606 for documentation/examples — never legitimate
# operator contacts. SEC fair-access policy bans traffic from placeholder
# User-Agents, so these must not reach live EDGAR calls.
_PLACEHOLDER_EMAIL_DOMAINS = {"example.com", "example.org", "example.net"}

# Settings that MUST be explicitly provided before SENTINEL_ENV=prod boots.
# Everything else keeps dev-friendly defaults and degrades gracefully.
_REQUIRED_PRODUCTION_SETTINGS = (
    ("sec_contact_email", "real operator contact address (SEC User-Agent)"),
    ("openai_api_key", "primary generation/embedding provider"),
    ("pinecone_api_key", "vector store"),
)


def resolve_secret(value: SecretStr | str | None) -> str | None:
    """Plain-text form of a credential setting, or None when unset/empty."""
    if value is None:
        return None
    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
    return raw or None


def is_placeholder_contact_email(email: str | None) -> bool:
    """True when an address is blank or a documentation placeholder — i.e. not
    something SEC fair-access policy would accept in a User-Agent.

    Matches reserved domains exactly, as subdomains (user@mail.example.com is
    as unusable as user@example.com), and tolerates the trailing-dot form
    ("user@example.com.").
    """
    if not email or "@" not in email:
        return True
    domain = email.rsplit("@", 1)[1].strip().lower().rstrip(".")
    if domain in _PLACEHOLDER_EMAIL_DOMAINS:
        return True
    return any(domain.endswith("." + d) for d in _PLACEHOLDER_EMAIL_DOMAINS)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM providers (Phase 2)
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
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
    ingest_batch_size: int = 128
    delete_before_reingest: bool = True
    # Pinecone rejects per-vector metadata over ~40KB; stay under it with headroom
    pinecone_metadata_cap_bytes: int = 38_000
    enable_llm_entity_extraction: bool = False

    # Query path (Phase 2)
    enable_llm_query_rewrite: bool = False
    rag_top_k: int = 12
    rag_excerpt_chars: int = 2000
    rag_context_char_budget: int = 20000
    api_max_question_chars: int = 4000

    # Vector store
    pinecone_api_key: SecretStr | None = None
    pinecone_environment: str | None = None  # provider-side env/region label (legacy naming)
    pinecone_index_name: str = "sentinel"
    embedding_dimension: int = 1536  # text-embedding-3-small (spec 8.2)
    pinecone_cloud: str = "aws"  # used only when provisioning via ensure_index()
    pinecone_region: str = "us-east-1"

    # News API (Phase 3)
    news_api_key: SecretStr | None = None
    news_api_provider: str = "financial_modeling_prep"

    # Langfuse (Phase 4)
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    # APEX adapter (Phase 6; disabled in adapters.yaml by default). SecretStr
    # because endpoints may embed tokens in the URL.
    apex_endpoint_url: SecretStr | None = None

    # Security & API Protection
    auth_enabled: bool = False
    auth_api_key: SecretStr | None = None
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = 120
    rate_limit_burst_limit: int = 30
    max_request_body_bytes: int = 1_048_576  # 1 MB max request size
    cors_allowed_origins: str = "*"
    allowed_hosts: str = "*"
    security_headers_enabled: bool = True

    # Observability & Logging
    log_format: str = "text"  # "text" for local dev, "json" for CloudWatch / staging / prod
    log_level: str = "INFO"

    # Runtime environment -> Pinecone namespace for dev/prod isolation (spec 8.2)
    sentinel_env: str = "dev"
    commit_sha: str = "dev"

    # SEC requires a descriptive User-Agent with contact info (spec 6.2).
    # Default is a placeholder: live EDGAR fetches refuse to run until a real
    # address is configured (see SecEdgarAdapter.fetch).
    sec_contact_email: str = "sentinel-operator@example.com"

    @model_validator(mode="after")
    def _validate_production_requirements(self) -> "Settings":
        if self.sentinel_env != "prod":
            return self
        blockers = self.production_blockers()
        if blockers:
            raise ValueError(
                "SENTINEL_ENV=prod requires explicit configuration; "
                f"missing/invalid: {', '.join(blockers)}. "
                "Set the listed environment variables (see .env.example) — "
                "the service refuses to boot half-configured in production."
            )
        return self

    def production_blockers(self) -> list[str]:
        """Human-readable names of unmet production requirements, empty when
        prod-ready. Dev/staging defaults are intentionally exempt."""
        if self.sentinel_env != "prod":
            return []
        blockers: list[str] = []
        if is_placeholder_contact_email(self.sec_contact_email):
            blockers.append("SEC_CONTACT_EMAIL")
        if resolve_secret(self.openai_api_key) is None:
            blockers.append("OPENAI_API_KEY")
        if resolve_secret(self.pinecone_api_key) is None:
            blockers.append("PINECONE_API_KEY")
        if self.auth_enabled and resolve_secret(self.auth_api_key) is None:
            blockers.append("AUTH_API_KEY (auth_enabled is true)")
        return blockers

    @property
    def is_auth_active(self) -> bool:
        """True when authentication is enabled and an API key is configured."""
        return self.auth_enabled and resolve_secret(self.auth_api_key) is not None

    @property
    def parsed_cors_origins(self) -> list[str]:
        """Parsed list of allowed CORS origins."""
        if not self.cors_allowed_origins:
            return ["*"]
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def parsed_allowed_hosts(self) -> list[str]:
        """Parsed list of allowed host headers."""
        if not self.allowed_hosts or self.allowed_hosts.strip() == "*":
            return ["*"]
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

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
    """Parse config/adapters.yaml, expanding ${VAR} references from the environment.

    An unset variable still expands to '' (so disabled-by-default blocks parse),
    but each unresolved reference is logged — audit M-3: silent substitution
    masked misconfiguration."""
    path = path or Path(__file__).resolve().parent / "adapters.yaml"
    raw = path.read_text(encoding="utf-8")

    warned: set[str] = set()

    def _expand(match: re.Match[str]) -> str:
        name = match.group(1)
        value = os.environ.get(name)
        if value is None and name not in warned:
            warned.add(name)
            logger.warning(
                "adapters.yaml references ${%s} but it is not set — expanding to '' "
                "(set the variable if this adapter should be active)",
                name,
            )
        return value if value is not None else ""

    expanded = _ENV_REF.sub(_expand, raw)
    return yaml.safe_load(expanded) or {}


def enabled_adapters(config: dict | None = None) -> list[str]:
    """Names of adapters whose `enabled` flag is true."""
    cfg = config if config is not None else load_adapters_config()
    return [name for name, conf in cfg.items() if isinstance(conf, dict) and conf.get("enabled")]
