"""Configuration contract tests (audit M-4 follow-up).

Covers production-mode gating (fail-fast on missing required settings),
credential SecretStr handling (repr-safe, resolve-once semantics), the
placeholder SEC contact guard, adapters.yaml ${VAR} expansion behavior,
and offline-safe defaults.
"""

import logging

import pytest
from pydantic import ValidationError

from config.settings import (
    enabled_adapters,
    is_placeholder_contact_email,
    load_adapters_config,
    resolve_secret,
)

# --------------------------------------------------------------------------
# Offline-safe defaults
# --------------------------------------------------------------------------


class TestOfflineDefaults:
    def test_defaults_configure_no_credentials(self, clean_settings):
        s = clean_settings()
        assert resolve_secret(s.openai_api_key) is None
        assert resolve_secret(s.pinecone_api_key) is None
        assert resolve_secret(s.news_api_key) is None
        assert resolve_secret(s.langfuse_public_key) is None
        assert resolve_secret(s.langfuse_secret_key) is None
        assert resolve_secret(s.apex_endpoint_url) is None

    def test_default_environment_is_dev(self, clean_settings):
        s = clean_settings()
        assert s.sentinel_env == "dev"
        assert s.namespace == "dev"

    def test_dev_mode_has_no_production_blockers_even_unconfigured(self, clean_settings):
        """Dev/demo must boot with zero configuration."""
        assert clean_settings().production_blockers() == []


# --------------------------------------------------------------------------
# resolve_secret / SecretStr hygiene
# --------------------------------------------------------------------------


class TestResolveSecret:
    def test_none_and_empty_resolve_to_none(self, clean_settings):
        s = clean_settings(openai_api_key="")
        assert resolve_secret(None) is None
        assert resolve_secret(s.openai_api_key) is None

    def test_value_round_trips(self, clean_settings):
        s = clean_settings(openai_api_key="sk-test")
        assert resolve_secret(s.openai_api_key) == "sk-test"

    def test_plain_string_passthrough(self):
        assert resolve_secret("raw-value") == "raw-value"

    def test_repr_masks_credentials(self, clean_settings):
        """The failure mode this hardening exists for: a Settings object
        logged/repr'd/dumped must never expose key material."""
        s = clean_settings(
            openai_api_key="sk-super-secret-value",
            news_api_key="fmp-secret",
        )
        rendered = repr(s)
        assert "sk-super-secret-value" not in rendered
        assert "fmp-secret" not in rendered


# --------------------------------------------------------------------------
# Production gating
# --------------------------------------------------------------------------


class TestProductionGating:
    def test_prod_with_everything_missing_raises_listing_all(self, clean_settings):
        with pytest.raises(ValidationError) as excinfo:
            clean_settings(sentinel_env="prod")
        message = str(excinfo.value)
        for var in ("SEC_CONTACT_EMAIL", "OPENAI_API_KEY", "PINECONE_API_KEY"):
            assert var in message, f"{var} missing from production failure message"

    def test_prod_with_placeholder_sec_email_raises(self, clean_settings):
        with pytest.raises(ValidationError) as excinfo:
            clean_settings(
                sentinel_env="prod",
                sec_contact_email="sentinel-operator@example.com",
                openai_api_key="sk-test",
                pinecone_api_key="pc-test",
            )
        assert "SEC_CONTACT_EMAIL" in str(excinfo.value)

    def test_prod_fully_configured_boots_clean(self, clean_settings):
        s = clean_settings(
            sentinel_env="prod",
            sec_contact_email="ops@sentinel.example.dev",  # not a reserved doc domain
            openai_api_key="sk-test",
            pinecone_api_key="pc-test",
        )
        assert s.production_blockers() == []

    def test_optional_providers_stay_optional_in_prod(self, clean_settings):
        """News/Langfuse/APEX degrade gracefully everywhere — never gate prod."""
        s = clean_settings(
            sentinel_env="prod",
            sec_contact_email="ops@sentinel.example.dev",
            openai_api_key="sk-test",
            pinecone_api_key="pc-test",
            news_api_key=None,
            langfuse_secret_key=None,
            apex_endpoint_url=None,
        )
        assert s.production_blockers() == []

    def test_staging_is_not_gated_like_prod(self, clean_settings):
        assert clean_settings(sentinel_env="staging").production_blockers() == []

    def test_prod_auth_enabled_requires_auth_api_key(self, clean_settings):
        # Auth enabled without key blocks prod boot
        with pytest.raises(ValueError, match="AUTH_API_KEY"):
            clean_settings(
                sentinel_env="prod",
                sec_contact_email="ops@sentinel.example.dev",
                openai_api_key="sk-test",
                pinecone_api_key="pc-test",
                auth_enabled=True,
                auth_api_key=None,
            )

        # Auth enabled with key passes
        s = clean_settings(
            sentinel_env="prod",
            sec_contact_email="ops@sentinel.example.dev",
            openai_api_key="sk-test",
            pinecone_api_key="pc-test",
            auth_enabled=True,
            auth_api_key="auth-secret-123",
        )
        assert s.production_blockers() == []


# --------------------------------------------------------------------------
# SEC contact email placeholder detection
# --------------------------------------------------------------------------


class TestPlaceholderEmailDetection:
    @pytest.mark.parametrize(
        ("email", "expected"),
        [
            (None, True),
            ("", True),
            ("   ", True),
            ("not-an-email", True),
            ("sentinel-operator@example.com", True),  # shipped default
            ("your-email@example.com", True),
            ("user@example.com", True),
            ("user@EXAMPLE.ORG", True),  # case-insensitive domain check
            ("user@example.net", True),
            ("user@mail.example.com", True),  # subdomain of a reserved domain
            ("user@sub.example.org", True),
            ("user@example.com.", True),  # trailing-dot FQDN form
            ("ops@sentinel.example.dev", False),
            ("kunal@parmar.dev", False),
            ("notexample.com@example.dev", False),  # reserved domain only as local part
        ],
    )
    def test_detection_table(self, email, expected):
        assert is_placeholder_contact_email(email) is expected

    def test_user_agent_carries_email(self, clean_settings):
        s = clean_settings(sec_contact_email="ops@sentinel.example.dev")
        assert s.sec_user_agent.endswith("(ops@sentinel.example.dev)")


# --------------------------------------------------------------------------
# Live-EDGAR guard: fetch refuses to run against sec.gov while unconfigured
# --------------------------------------------------------------------------


class _RecordingSession:
    """Duck-typed session that records calls — proves no network I/O happens
    before the guard fires."""

    def __init__(self):
        self.headers: dict = {}
        self.calls: list[str] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        raise AssertionError("live HTTP attempted despite placeholder contact email")


class TestSecLiveUseGuard:
    def _adapter(self, settings):
        from data_sources.sec_edgar import SecEdgarAdapter

        adapter = SecEdgarAdapter(settings=settings, session=_RecordingSession())
        adapter._min_interval = 0.0
        return adapter

    def test_fetch_refuses_placeholder_email_before_any_http(self, clean_settings):
        from data_sources.sec_edgar import SecContactEmailConfigError

        s = clean_settings()  # default placeholder contact
        adapter = self._adapter(s)
        with pytest.raises(SecContactEmailConfigError, match="SEC_CONTACT_EMAIL"):
            adapter.fetch({"ticker": "AAPL", "filing_type": "10-K"})
        assert adapter.session.calls == []  # guard fired before any request

    def test_fetch_refuses_example_dot_com_addresses(self, clean_settings):
        from data_sources.sec_edgar import SecContactEmailConfigError

        s = clean_settings(sec_contact_email="someone@example.com")
        with pytest.raises(SecContactEmailConfigError):
            self._adapter(s).fetch({"query": "risk factors"})

    def test_configured_address_passes_the_gate(self, clean_settings):
        """With a configured address, fetch proceeds past the guard into the
        HTTP layer (which our recording session serves)."""
        from data_sources.sec_edgar import SecContactEmailConfigError

        s = clean_settings(sec_contact_email="ops@sentinel.example.dev")
        adapter = self._adapter(s)
        try:
            adapter.fetch({"ticker": "AAPL", "filing_type": "10-K"})
        except SecContactEmailConfigError:
            pytest.fail("guard fired despite configured address")
        except Exception:  # noqa: BLE001 — fake session data shape errors are fine
            pass  # we only care that the placeholder gate was passed


# --------------------------------------------------------------------------
# adapters.yaml parsing
# --------------------------------------------------------------------------


class TestAdaptersConfig:
    def test_shipped_config_enables_edgar_and_news_only(self):
        cfg = load_adapters_config()
        assert sorted(enabled_adapters(cfg)) == ["news_api", "sec_edgar"]
        assert cfg["apex"]["enabled"] is False

    def test_missing_env_reference_expands_empty_and_warns(self, tmp_path, caplog):
        config_file = tmp_path / "adapters.yaml"
        config_file.write_text(
            "apex:\n  enabled: false\n  endpoint: ${DEFINITELY_NOT_SET_VAR_123}\n",
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING):
            cfg = load_adapters_config(config_file)
        # Expanded '' parses as YAML null — either way it is falsy/absent.
        assert not cfg["apex"]["endpoint"]
        assert any("DEFINITELY_NOT_SET_VAR_123" in rec.message for rec in caplog.records)

    def test_set_env_reference_expands_value(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SET_VAR_123", "https://apex.internal")
        config_file = tmp_path / "adapters.yaml"
        config_file.write_text(
            "apex:\n  enabled: false\n  endpoint: ${SET_VAR_123}\n",
            encoding="utf-8",
        )
        assert load_adapters_config(config_file)["apex"]["endpoint"] == "https://apex.internal"
