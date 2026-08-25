# Sentinel — Deployment & Operations

Covers containerization, the local Compose stack, environment configuration,
health/readiness semantics, the Terraform foundation, and secret management.
Status after this milestone: **nothing has been deployed and no cloud
resources exist.** Every artifact below runs locally or in CI only; no AWS
account, VPC, cluster, bucket, or secret store has been created or touched.

---

## 1. Local development (no containers)

```bash
make setup            # .venv from backend/requirements-dev.txt
cp .env.example .env  # optional — empty file is a valid offline config
make run              # uvicorn on 127.0.0.1:8000
make check            # ruff format/lint + mypy + offline test suite
```

`backend/` is a **source root** (absolute imports like `from models.schemas
import Chunk`). Tooling resolves it via `pythonpath` in `pyproject.toml`,
`PYTHONPATH=backend`, and — inside images — `PYTHONPATH=/app/backend`. There
are no runtime `sys.path` hacks anywhere.

## 2. Image build

`infra/Dockerfile.backend` (build context: repo root):

```bash
docker build -f infra/Dockerfile.backend --target production -t sentinel-backend:local .
```

| Property | Value |
|---|---|
| Base | `python:3.11-slim-trixie` (pinned tag) |
| Dependencies | `backend/requirements-prod-lock.txt` — runtime-only subset of the dev lock, identical versions via `-c` constraint (`make lock` regenerates both) |
| User | uid/gid 10001 `sentinel:sentinel`, nologin shell; code+venv root-owned |
| Filesystem | works under read-only root; writable state confined to `/tmp` |
| Imports | `ENV PYTHONPATH=/app/backend` — source-root layout baked in |
| Server | exec-form `uvicorn api.main:app --host 0.0.0.0 --port 8000` (PID 1) |
| Graceful shutdown | SIGTERM → stop accepting → drain → lifespan tracer-flush; bounded by `--timeout-graceful-shutdown 20` |
| HEALTHCHECK | stdlib urllib GET `/health` (liveness only — see §5) |
| Network at build | base-image pull + pinned pip install only |

A second target, `test`, installs the full runtime+dev lockfile and runs the
hermetic pytest suite inside Linux — CI executes it on every push:

```bash
docker build -f infra/Dockerfile.backend --target test -t sentinel-backend:test .
docker run --rm sentinel-backend:test
```

The default build target is `production`; the dev/test image exists only via
explicit `--target test` and never changes production behavior.

## 3. Docker Compose stack

`infra/docker-compose.yml` — backend only. No APEX service (Sentinel is fully
standalone per spec §6.4), and no frontend service yet: the Phase 5 Next.js
UI will be added as a sibling service consuming this API over REST.

### Offline start (zero credentials)

```bash
cd <repo-root>
docker compose -f infra/docker-compose.yml up --build
curl -s localhost:8000/health   # {"status":"ok",...}
```

Requires nothing: no `.env`, no keys. The API serves `/health` (200);
`/ready` returns 503 degraded until providers are configured. Requires
Docker Compose v2.24+ (`env_file.required`).

### Provider-enabled start

Credentials reach the container through exactly one channel: `env_file:
../.env` (gitignored). Create it from the template and fill in what you have:

```bash
cp .env.example .env   # set OPENAI_API_KEY / PINECONE_API_KEY / NEWS_API_KEY ...
docker compose -f infra/docker-compose.yml up --build
curl -s localhost:8000/ready   # 200 once embedding + vector store are configured
```

All other container configuration — credentials and non-secret tunables alike
(`SENTINEL_ENV`, model names, provider order, `SEC_CONTACT_EMAIL`) — reaches the
container through that same single channel: `../.env` via `env_file`. The
compose file declares **no `environment:` block on purpose**: Compose gives
`environment:` precedence over `env_file:`, and its `${VAR:-}` interpolation
resolves against the shell / project directory (`infra/`), never the repo-root
`.env` — so any entry there could silently replace a configured value with an
empty string. Unset variables simply fall back to the safe defaults in
`backend/config/settings.py`, which is what makes offline boot work.

> Ollama note: the container's `OLLAMA_BASE_URL` default points to
> `127.0.0.1` *inside* the container. To use a host daemon set
> `OLLAMA_BASE_URL=http://host.docker.internal:11434` in `.env`.

### Stack hardening

Read-only root filesystem · `cap_drop: ALL` · `no-new-privileges` · bounded
tmpfs `/tmp` · named bridge network `sentinel` · log rotation (10MB × 3) ·
restart policy `unless-stopped` · `stop_grace_period: 25s` (headroom above
uvicorn's 20s drain bound — Compose's 10s default would SIGKILL mid-drain).

**Port exposure:** v1 ships no authentication (spec §17). The compose port
mapping defaults to loopback:

```
"${SENTINEL_API_BIND:-127.0.0.1}:${SENTINEL_API_PORT:-8000}:8000"
```

These two variables (plus `SENTINEL_IMAGE_TAG`) are consumed by *Compose*
itself, so they interpolate from the shell or from `infra/.env` (the project
directory) — not from the repo-root `.env`. Never override
`SENTINEL_API_BIND` to `0.0.0.0` on a reachable host without adding auth or a
gateway that enforces access control first.

## 4. Environment variables

Full annotated list: `.env.example`. Summary:

**Required before production boot** (`SENTINEL_ENV=prod` fails fast otherwise):

| Variable | Purpose |
|---|---|
| `SEC_CONTACT_EMAIL` | Real operator contact for SEC User-Agent (placeholder/example.com domains refused for live EDGAR use in every environment) |
| `OPENAI_API_KEY` | Primary generation + embeddings |
| `PINECONE_API_KEY` | Vector store |

**Optional everywhere** (degrade gracefully): `NEWS_API_KEY`,
`NEWS_API_PROVIDER`, `LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST`,
`OLLAMA_BASE_URL/…MODELS`, `APEX_ENDPOINT_URL` (adapter disabled),
`LLM_PROVIDER_ORDER`, model names, retry/RAG tuning knobs,
`PINECONE_INDEX_NAME`, `SENTINEL_ENV` (dev/staging/prod).

Compose-only knobs: `SENTINEL_IMAGE_TAG`, `SENTINEL_API_BIND`,
`SENTINEL_API_PORT`.

Secret-handling rules enforced in code: every credential field is a pydantic
`SecretStr` — logging/repr/dumping a Settings object shows `**********`;
SDK hand-off sites resolve once via `resolve_secret()`; nothing logs
configuration containing secrets.

## 5. Health vs readiness

| Endpoint | Success | Degraded behavior | Meaning |
|---|---|---|---|
| `GET /health` | 200 `{status, version, env}` | always 200 while process lives | liveness — restart me if this fails |
| `GET /ready` | 200 `{status:"ready", checks}` | 503 `not_ready` + checks detail | can actually serve `/query` and `/ingest`: embedding provider available AND vector store configured |

The container HEALTHCHECK intentionally uses `/health` only — an unconfigured
but correctly-running service must not be restarted into a crashloop.
Orchestrators should gate traffic routing on `/ready`.

## 6. Terraform foundation (`infra/terraform/`)

Deployment-neutral skeleton targeting AWS. Version-pinned (`>= 1.9, < 2`;
AWS provider `~> 6.0`), validated variables, zero resources — extension
points for networking, hosting, secrets, logging, monitoring, IAM, and
environment separation are documented inline in `main.tf`. Details and the
remote-state bootstrap procedure: `infra/terraform/README.md`.

```bash
cd infra/terraform
terraform fmt -check -recursive   # formatting gate (CI)
terraform init -backend=false     # providers only, no state
terraform validate                # structural validation (CI)
# plan/apply happen ONLY later, against a chosen dev account, with envs/*.tfvars
```

Environments (dev/staging/prod) will differ exclusively by tfvars and remote-
state key prefixes (`sentinel/<environment>/terraform.tfstate`) — never shared
state or workspaces.

## 7. Secret management

| Layer | Rule |
|---|---|
| Git | `.env*` gitignored (except `.env.example`); gitleaks scans full history in CI; local pattern scan available |
| Images | `.dockerignore` hard-excludes `.env*` from the build context; Dockerfile contains no credential literals (contract-tested) |
| Compose | credentials flow only via `env_file: ../.env`; never in `environment:` interpolation |
| Runtime objects | `SecretStr` settings fields mask repr/logs; tracer redacts secret-like metadata keys |
| Cloud (future) | Secrets Manager per environment read by the ECS execution role at task start; values never enter tfvars/task defs/git |

## 8. Staging vs production expectations

| Aspect | dev (default) | staging | prod |
|---|---|---|---|
| Boot requirements | none | none planned; tfvars-driven | SEC email + OpenAI + Pinecone mandatory (fail-fast) |
| `SENTINEL_ENV` | `dev` | `staging` | `prod` (→ Pinecone namespace isolation) |
| Data | throwaway namespace | scrubbed copies only | real filings/news |
| Exposure | loopback only | private network behind gateway | private network + auth/gateway BEFORE any exposure |
| State | local compose | remote TF state `staging/` prefix | remote TF state `prod/` prefix |

Production readiness beyond this milestone: authentication/rate limiting
(v1 non-goal), HTTPS termination at a gateway, alarm routing, and backup/
retention policy — all tracked as pre-prod gates, none implemented here.

## 9. CI gates

`.github/workflows/ci.yml` runs five independent jobs: host quality gates;
production image build + contract checks (non-root UID 10001, no pytest in
image, import safety, offline boot with `/health`=200 and `/ready`=503); the
test suite executed inside Linux via the `test` target; compose config
validation; terraform fmt/init/validate; gitleaks secret scanning over full
history. All checks are offline except CI's own package/image/action
downloads.
