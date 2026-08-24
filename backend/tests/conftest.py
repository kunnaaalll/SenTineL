"""Puts backend/ on sys.path so tests can import packages directly
(models, ingestion, ...) regardless of where pytest is invoked from.

Also scrubs provider/credential env vars so availability logic is tested
hermetically — a developer's exported OPENAI_API_KEY must never flip a
test's expected provider state.
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest  # noqa: E402

_CREDENTIAL_ENV_VARS = [
    "OPENAI_API_KEY",
    "PINECONE_API_KEY",
    "NEWS_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "APEX_ENDPOINT_URL",
    "OLLAMA_BASE_URL",
]


@pytest.fixture(autouse=True)
def _scrub_credential_env(monkeypatch):
    for name in _CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def clean_settings():
    """Settings builder isolated from any repo .env file and the environment."""
    from config.settings import Settings

    def build(**overrides):
        return Settings(_env_file=None, **overrides)

    return build
