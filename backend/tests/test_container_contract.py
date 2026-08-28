"""Infrastructure contract tests — container, compose, and import-safety.

These run in the offline host suite (pure file reads + one subprocess) so a
Dockerfile/compose regression fails `pytest` locally and in CI before it ever
reaches an image build. The authoritative build/boot checks live in the CI
`container` job; this module pins the contracts that build relies on.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "infra" / "Dockerfile.backend"
DOCKERFILE_FRONTEND = REPO_ROOT / "infra" / "Dockerfile.frontend"
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.yml"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"

_CREDENTIAL_VARS = (
    "OPENAI_API_KEY",
    "PINECONE_API_KEY",
    "NEWS_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "APEX_ENDPOINT_URL",
    "SENTINEL_ENV",
    "SEC_CONTACT_EMAIL",
)


@pytest.fixture(scope="module")
def dockerfile_text() -> str:
    assert DOCKERFILE.exists(), f"{DOCKERFILE} missing"
    return DOCKERFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def frontend_dockerfile_text() -> str:
    assert DOCKERFILE_FRONTEND.exists(), f"{DOCKERFILE_FRONTEND} missing"
    return DOCKERFILE_FRONTEND.read_text(encoding="utf-8")


def _stages(text: str) -> list[tuple[str, str]]:
    """Split Dockerfile text into (stage_name, body) pairs in declaration order."""
    stages: list[tuple[str, str]] = []
    current_name = "global"
    current_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("FROM "):
            if current_lines:
                stages.append((current_name, "\n".join(current_lines)))
            parts = line.split()
            # FROM image [AS name]
            current_name = (
                parts[3] if len(parts) >= 4 and parts[2].upper() == "AS" else f"stage{len(stages)}"
            )
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        stages.append((current_name, "\n".join(current_lines)))
    return stages


# --------------------------------------------------------------------------
# Dockerfile contract — Backend
# --------------------------------------------------------------------------


class TestDockerfileContract:
    def test_base_images_are_pinned_python_311_slim(self, dockerfile_text):
        bases = [
            line.split()[1] for line in dockerfile_text.splitlines() if line.startswith("FROM ")
        ]
        assert bases, "no FROM lines found"
        for base in bases:
            assert base == "python:3.11-slim-trixie", f"unexpected base {base}"

    def test_installs_from_locked_dependency_files_only(self, dockerfile_text):
        assert "requirements-prod-lock.txt" in dockerfile_text
        assert "requirements-lock.txt" in dockerfile_text  # test stage
        # Ranges must not leak into image builds.
        assert "-r requirements.txt" not in dockerfile_text

    def test_production_stage_runs_as_non_root(self, dockerfile_text):
        production_name = "production"
        production_body = dict(_stages(dockerfile_text))[production_name]
        user_lines = [line for line in production_body.splitlines() if line.startswith("USER ")]
        assert user_lines == ["USER sentinel:sentinel"], (
            "production stage must end up running as the unprivileged sentinel user"
        )

    def test_production_stage_has_healthcheck_against_local_health_endpoint(self, dockerfile_text):
        production_body = dict(_stages(dockerfile_text))["production"]
        assert "HEALTHCHECK" in production_body
        assert "/health" in production_body

    def test_graceful_shutdown_is_bounded(self, dockerfile_text):
        assert "--timeout-graceful-shutdown" in dockerfile_text

    def test_cmd_is_exec_form_uvicorn(self, dockerfile_text):
        production_body = dict(_stages(dockerfile_text))["production"]
        cmd_lines = [ln for ln in production_body.splitlines() if ln.startswith("CMD ")]
        assert len(cmd_lines) == 1
        assert cmd_lines[0].startswith('CMD ["uvicorn"'), "CMD must be exec-form"

    def test_no_credentials_embedded_in_any_layer(self, dockerfile_text):
        forbidden = (
            "OPENAI_API_KEY=",
            "PINECONE_API_KEY=",
            "NEWS_API_KEY=",
            "LANGFUSE_SECRET_KEY=",
            "sk-",
            "apikey",
        )
        for line in dockerfile_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for token in forbidden:
                assert token.lower() not in stripped.lower(), f"suspicious literal: {line}"

    def test_test_stage_puts_its_venv_on_path(self, dockerfile_text):
        """Regression: the test stage's CMD must run the venv interpreter that
        holds pytest, not /usr/local/bin/python (which silently lacks it)."""
        test_body = dict(_stages(dockerfile_text))["test"]
        assert 'ENV PATH="/opt/venv/bin:$PATH"' in test_body

    def test_source_root_layout_is_explicit(self, dockerfile_text):
        """backend/ is a source root: PYTHONPATH must carry /app/backend — no
        runtime sys.path hacks."""
        assert "PYTHONPATH=/app/backend" in dockerfile_text
        code_lines = [
            line
            for line in dockerfile_text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        joined = "\n".join(code_lines)
        assert "sys.path" not in joined, "no runtime path hacks allowed"


# --------------------------------------------------------------------------
# Dockerfile contract — Frontend
# --------------------------------------------------------------------------


class TestFrontendDockerfileContract:
    def test_base_images_are_pinned_node_alpine(self, frontend_dockerfile_text):
        bases = [
            line.split()[1]
            for line in frontend_dockerfile_text.splitlines()
            if line.startswith("FROM ")
        ]
        assert bases, "no FROM lines found"
        for base in bases:
            assert base.startswith("node:20-alpine"), f"unexpected base {base}"

    def test_installs_from_locked_package_json_only(self, frontend_dockerfile_text):
        assert "package-lock.json" in frontend_dockerfile_text
        assert "npm ci" in frontend_dockerfile_text

    def test_production_stage_runs_as_non_root_node(self, frontend_dockerfile_text):
        stages = dict(_stages(frontend_dockerfile_text))
        assert "production" in stages
        production_body = stages["production"]
        user_lines = [line for line in production_body.splitlines() if line.startswith("USER ")]
        assert user_lines == ["USER node"], (
            "production stage must end up running as the unprivileged node user"
        )

    def test_production_stage_has_healthcheck(self, frontend_dockerfile_text):
        stages = dict(_stages(frontend_dockerfile_text))
        production_body = stages["production"]
        assert "HEALTHCHECK" in production_body
        assert "/health" in production_body

    def test_cmd_is_exec_form_node(self, frontend_dockerfile_text):
        stages = dict(_stages(frontend_dockerfile_text))
        production_body = stages["production"]
        cmd_lines = [ln for ln in production_body.splitlines() if ln.startswith("CMD ")]
        assert len(cmd_lines) == 1
        assert cmd_lines[0].startswith('CMD ["node"'), "CMD must be exec-form"

    def test_no_credentials_embedded_in_any_layer(self, frontend_dockerfile_text):
        forbidden = (
            "OPENAI_API_KEY=",
            "PINECONE_API_KEY=",
            "NEWS_API_KEY=",
            "LANGFUSE_SECRET_KEY=",
            "sk-",
            "apikey",
        )
        for line in frontend_dockerfile_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for token in forbidden:
                assert token.lower() not in stripped.lower(), f"suspicious literal: {line}"


class TestDockerignoreContract:
    def test_secrets_never_enter_build_context(self):
        text = DOCKERIGNORE.read_text(encoding="utf-8")
        assert ".env" in text
        # A negation would re-include secret files — there is no legitimate case.
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("!"):
                assert ".env" not in stripped, f"dockerignore re-includes secrets: {stripped}"


# --------------------------------------------------------------------------
# Compose contract
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def compose_config() -> dict:
    assert COMPOSE_FILE.exists(), f"{COMPOSE_FILE} missing"
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


class TestComposeContract:
    def test_services_present(self, compose_config):
        services = set(compose_config["services"])
        assert services == {"backend", "frontend"}, f"unexpected service set: {services}"

    def test_no_apex(self, compose_config):
        """The optional APEX adapter is never a stack service."""
        services = set(compose_config["services"])
        assert not any("apex" in name for name in services)

    def test_publishes_on_loopback_by_default(self, compose_config):
        backend_ports = compose_config["services"]["backend"]["ports"]
        assert any("${SENTINEL_API_BIND:-127.0.0.1}" in p for p in backend_ports), (
            "v1 has no auth — the default backend publish target must be loopback"
        )
        frontend_ports = compose_config["services"]["frontend"]["ports"]
        assert any("${SENTINEL_FRONTEND_BIND:-127.0.0.1}" in p for p in frontend_ports), (
            "v1 frontend default publish target must be loopback"
        )

    def test_hardening_options_present(self, compose_config):
        backend = compose_config["services"]["backend"]
        assert backend["read_only"] is True
        assert "ALL" in backend["cap_drop"]
        assert "no-new-privileges:true" in backend["security_opt"]
        assert any(t.startswith("/tmp:") or t == "/tmp" for t in backend["tmpfs"])
        assert backend["restart"] == "unless-stopped"

        frontend = compose_config["services"]["frontend"]
        assert "no-new-privileges:true" in frontend["security_opt"]
        assert frontend["restart"] == "unless-stopped"

    def test_frontend_wiring(self, compose_config):
        frontend = compose_config["services"]["frontend"]
        assert "backend" in frontend.get("depends_on", [])
        assert "sentinel" in frontend.get("networks", [])
        env = frontend.get("environment", [])
        assert any("BACKEND_ORIGIN=" in str(item) for item in env)

    def test_stop_grace_period_exceeds_uvicorn_drain_bound(self, compose_config):
        """uvicorn drains up to 20s (--timeout-graceful-shutdown); Compose's
        default stop grace is only 10s and would SIGKILL mid-drain."""
        grace = compose_config["services"]["backend"]["stop_grace_period"]
        seconds = float(grace.rstrip("s"))
        assert seconds > 20, f"stop_grace_period {grace} cannot cover a 20s drain"

    def test_named_bridge_network_not_default(self, compose_config):
        networks = compose_config["networks"]
        assert set(networks) == {"sentinel"}
        assert networks["sentinel"]["driver"] == "bridge"
        assert "sentinel" in compose_config["services"]["backend"]["networks"]
        assert "sentinel" in compose_config["services"]["frontend"]["networks"]

    def test_backend_no_environment_block_so_env_file_cannot_be_shadowed(self, compose_config):
        """`environment:` outranks `env_file:` per the Compose spec, and its
        ${VAR:-} interpolations resolve against the shell / project-dir .env —
        never the repo-root .env. Any entry there could silently replace a
        configured value with an empty string, so the block must not exist:
        ALL container config flows through env_file or settings.py defaults.
        """
        backend = compose_config["services"]["backend"]
        assert "environment" not in backend, (
            "environment: block would shadow env_file values; "
            "container config must come from ../.env via env_file only"
        )

    def test_env_file_is_optional_so_stack_boots_offline(self, compose_config):
        entries = compose_config["services"]["backend"]["env_file"]
        paths = {(e["path"], e.get("required")) for e in entries}
        assert ("../.env", False) in paths

    def test_interpolation_limited_to_compose_owned_knobs(self, compose_config):
        """The only ${...} substitutions allowed are the Compose-owned knobs
        (image tag, bind hosts, ports) — nothing that reaches container secrets."""
        text = COMPOSE_FILE.read_text(encoding="utf-8")
        import re

        referenced = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)(?::-)?", text))
        allowed = {
            "SENTINEL_IMAGE_TAG",
            "SENTINEL_API_BIND",
            "SENTINEL_API_PORT",
            "SENTINEL_FRONTEND_BIND",
            "SENTINEL_FRONTEND_PORT",
        }
        assert referenced <= allowed, f"unexpected interpolated vars: {referenced - allowed}"


# --------------------------------------------------------------------------
# Container-compatible imports (the audit's H-1 resolution)
# --------------------------------------------------------------------------


def _container_like_import(module: str) -> subprocess.CompletedProcess[str]:
    """Import `module` exactly like the image does: PYTHONPATH=backend,
    working directory OUTSIDE the repo, credentials scrubbed from env."""
    env = {k: v for k, v in os.environ.items() if k not in _CREDENTIAL_VARS and k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(REPO_ROOT / "backend")
    env.setdefault("PATH", "/usr/bin:/bin")
    return subprocess.run(
        [sys.executable, "-c", f"import {module}; print('{module} ok')"],
        capture_output=True,
        text=True,
        cwd="/",  # outside the repo entirely — no cwd-dependent resolution
        env=env,
        timeout=120,
        check=False,
    )


class TestContainerCompatibleImports:
    @pytest.mark.parametrize("module", ["api.main", "config.settings", "agents.graph"])
    def test_modules_import_without_repo_cwd_or_credentials(self, module):
        result = _container_like_import(module)
        assert result.returncode == 0, (
            f"{module} failed to import in container-like conditions:\n"
            f"{result.stdout}\n{result.stderr}"
        )
        assert "ok" in result.stdout

    def test_app_module_creates_application_offline(self):
        """api.main builds the full FastAPI app at import time; that must
        succeed with zero credentials present."""
        result = _container_like_import("api.main")
        combined = result.stdout + result.stderr
        assert "Traceback" not in combined
