# Sentinel

Agentic financial research copilot — RAG over SEC filings, earnings-call transcripts, and market news, producing cited natural-language answers. Simple questions go through a naive RAG chain; complex multi-entity questions go through a LangGraph agent team (Phase 3–4). Every LLM call is traced in Langfuse when configured.

Full architecture, API contracts, and build phases: see `SENTINEL_SPEC.md`. Phase 2 deep-dive: `docs/ARCHITECTURE.md`, `docs/API.md`. Phase 3 deep-dive: `docs/AGENT_DESIGN.md`.

## Status: Phase 3 + infrastructure foundations

Latest milestone (Phase 3 code unchanged): Git initialized, backend
containerized (`infra/Dockerfile.backend`), offline-capable Compose stack,
production config hardening (fail-fast prod mode, SecretStr credentials,
live-EDGAR contact gate), Terraform skeleton under `infra/terraform/`, and a
five-job CI pipeline including in-container tests and secret scanning.
**No cloud resources were provisioned** — deployment remains future work.
See `docs/DEPLOYMENT.md`.

Previously — Phase 3: production news adapter, LangGraph agent team, routed API:

Implemented:

- **Agent team (`backend/agents/`)** — typed `AgentState` contract plus a compiled LangGraph graph (`fetch → extract → (compare?) → synthesize`) serving multi-hop questions. Deterministic routing keeps the proven simple RAG path untouched for single-entity questions; `/agents/query` forces the agent path. Every node degrades to grounded partial output instead of raising; fetch live-ingestion is budgeted and loop-protected; extract enforces server-side provenance; compare aligns entities/periods flagging every gap; synthesize validates inline `[n]` citations against real chunks. See `docs/AGENT_DESIGN.md`.
- **Production news adapter (`backend/data_sources/news_api.py`)** — `DataSourceAdapter` implementation defaulting to Financial Modeling Prep with a provider registry, ticker/date-range/keyword queries, bounded pagination, retry/backoff with `Retry-After`, no retry on auth/invalid-request, deterministic dedup by URL/content hash, HTML-sanitized text, and key-safe logging. Unavailable (no key) rather than broken when unconfigured.
- `backend/api/` — adds automatic complexity routing on `POST /query` and forced multi-agent execution on `POST /agents/query`; same response shape (`answer`, `citations`, `agent_path`, `trace_url`) on both.
- Phase 2: provider fallback engine, Langfuse-or-noop tracing, ingestion pipeline, simple RAG chain with enforced citations.
- Phase 1 (data layer): SEC EDGAR adapter (retry/backoff), financial chunker, Pinecone store (`delete_source` + metadata cap).

Not yet (later phases): frontend chat UI, real cloud deployment, auth, APEX adapter, fine-tuning.

## Setup

```bash
make setup            # creates .venv (Python 3.11) and installs backend/requirements-dev.txt
cp .env.example .env  # optional — an empty file is a valid offline configuration
```

### Docker quickstart

The backend ships as a hardened production image (non-root, read-only-FS
compatible, locked runtime-only dependencies) with a local Compose stack:

```bash
# fully offline — no credentials needed to boot:
docker compose -f infra/docker-compose.yml up --build

# provider-enabled: put credentials in the gitignored .env first
cp .env.example .env   # fill in OPENAI_API_KEY / PINECONE_API_KEY / ...
docker compose -f infra/docker-compose.yml up --build
```

`/health` is 200 while the process lives; `/ready` returns 503 until the
embedding provider + vector store are configured. The stack publishes on
127.0.0.1 only — v1 has no auth. Details, image contract, and Terraform
workflow: `docs/DEPLOYMENT.md`.

Runtime dependencies live in `backend/requirements.txt`; dev tools in
`backend/requirements-dev.txt`. Reproducible installs use the pinned
`backend/requirements-lock.txt`, and the production image installs the
runtime-only subset `backend/requirements-prod-lock.txt` — regenerate both
with `make lock` after changing either requirements file. `langfuse` is
intentionally optional: install it yourself to enable tracing. `langgraph`
(Phase 3 agent graph) is a normal dependency and works fully offline.

### News provider configuration

The news adapter defaults to Financial Modeling Prep. Set `NEWS_API_KEY` to
enable it; without a key, `GET /sources` reports `news_api: false`, the fetch
agent says so explicitly instead of failing, and no network calls are made.
`NEWS_API_PROVIDER` selects the registry entry (only
`financial_modeling_prep` ships today; adding one means an endpoint, a param
builder, and a payload parser in `news_api.py`).

### Environment variables

Core (spec section 14.2): `OPENAI_API_KEY`, `PINECONE_API_KEY`,
`PINECONE_INDEX_NAME`, `NEWS_API_KEY` (Phase 3), `LANGFUSE_PUBLIC_KEY` /
`LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST`, `APEX_ENDPOINT_URL` (Phase 6).

Phase 2 additions (all have sensible defaults — see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER_ORDER` | `openai,ollama` | fallback chain; unavailable providers skipped |
| `OPENAI_GENERATION_MODEL` / `OPENAI_EMBEDDING_MODEL` | `gpt-4o-mini` / `text-embedding-3-small` | model selection |
| `OLLAMA_BASE_URL` / `OLLAMA_GENERATION_MODEL` / `OLLAMA_EMBEDDING_MODEL` | localhost / `llama3.1` / *(unset = embeddings off)* | local fallback |
| `LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` / `LLM_BACKOFF_BASE_SECONDS` | 60 / 2 / 0.5 | retry policy (auth & invalid-request never retried) |
| `SENTINEL_ENV` | `dev` | Pinecone namespace (`dev`/`prod` isolation) |
| `SEC_CONTACT_EMAIL` | placeholder | **set a real address before live EDGAR traffic** — SEC fair-access policy |
| `DELETE_BEFORE_REINGEST` | `true` | wipe a source's vectors before re-adding |
| `PINECONE_METADATA_CAP_BYTES` | 38000 | stays under Pinecone's ~40KB per-vector metadata limit |
| `ENABLE_LLM_ENTITY_EXTRACTION` / `ENABLE_LLM_QUERY_REWRITE` | `false` | optional LLM passes; deterministic versions always run |
| `RAG_TOP_K` / `RAG_EXCERPT_CHARS` / `RAG_CONTEXT_CHAR_BUDGET` | 6 / 1600 / 9000 | retrieval shaping |

### Provider fallback behavior

`generate()`/`embed()` walk `LLM_PROVIDER_ORDER`: unavailable providers are
skipped (availability cached ~30s); transient failures and rate limits retry
with exponential backoff (honoring `Retry-After`); authentication errors fall
through to the next provider without retry; invalid-request errors abort
immediately (the payload, not the provider, is at fault). Every result is
annotated with provider, model, usage, latency, and estimated cost.

### SEC contact email

SEC requires a descriptive User-Agent with a genuine contact address. The
placeholder default is refused for **live EDGAR use in every environment**
(`SecEdgarAdapter.fetch` raises before any network call), and
`SENTINEL_ENV=prod` refuses to boot without it. Set in `.env`:

```
SEC_CONTACT_EMAIL=you@yourdomain.com
```

The placeholder default risks an IP ban under SEC fair-access enforcement.

### Production fail-fast mode

With `SENTINEL_ENV=prod`, configuration validation requires `SEC_CONTACT_EMAIL`,
`OPENAI_API_KEY`, and `PINECONE_API_KEY`; anything missing aborts startup with
a message naming each variable. Optional providers (news, Langfuse, APEX,
Ollama) stay optional everywhere and degrade gracefully. All credential fields
are pydantic `SecretStr` — logging or repr-ing settings never exposes values.

## Run the API

```bash
make run        # http://127.0.0.1:8000 — docs at /docs
```

Local-only by design until authentication exists (Phase 5+). Example:

```bash
curl -s localhost:8000/ingest -H 'content-type: application/json' \
  -d '{"ticker": "AAPL", "filing_type": "10-K", "limit": 1}'
# simple question -> automatic RAG path
curl -s localhost:8000/query -H 'content-type: application/json' \
  -d '{"question": "What was Apple total net sales in fiscal 2024?"}'
# multi-entity question / forced agent team (fetch->extract->compare->synthesize)
curl -s localhost:8000/agents/query -H 'content-type: application/json' \
  -d '{"question": "Compare AAPL and MSFT revenue for FY2024"}'
```

`agent_path` in every response shows the executed steps (`classify` first,
then either the RAG chain's steps or the agent nodes), so you can always see
which path answered.

## Tests

The full suite is offline — scripted fake providers, an in-memory vector
store, canned adapters, recording tracers, and a fake OpenAI SDK module. No
test calls OpenAI, Ollama, Pinecone, Langfuse, SEC, or any news API; credential
env vars are scrubbed per test.

```bash
make test                                                    # full suite
make test-one T="backend/tests/test_pipeline.py::TestIdempotency"
```

## Quality gates

Configured in the root `pyproject.toml`:

```bash
make fmt         # ruff format + safe lint autofixes
make lint        # ruff format --check + ruff check
make typecheck   # mypy
make check       # all of the above + tests
```

CI (`.github/workflows/ci.yml`) runs five independent jobs on every push/PR:
host quality gates (ruff/mypy/pytest from the lockfile), production image
build + contract checks (non-root UID, no dev packages, import safety,
offline boot asserting `/health`=200 and `/ready`=503), the test suite
executed inside a Linux container, compose config validation, Terraform
fmt/init/validate, and gitleaks secret scanning. Pre-commit hooks
(`make hooks`) run the fast subset locally.

## Layout

Repository structure mirrors `SENTINEL_SPEC.md` section 4: `backend/` (API +
pipelines), `frontend/` (Next.js chat UI, Phase 5 — placeholder dirs only
today), `infra/` (`Dockerfile.backend`, `docker-compose.yml`, and a
resource-free Terraform skeleton in `terraform/`), `docs/`.

Sentinel is fully standalone — no dependency on any other project. The
optional APEX adapter (Phase 6) is disabled by default and never a hard
dependency.

## Known limitations (Phase 3)

- Routing is deterministic heuristics (tickers/comparison words/periods), not
  an LLM classifier — unusual phrasings may ride the simple path; `/agents/query`
  is the explicit escape hatch.
- Live ingestion during agent queries is deliberately bounded (2 ingests/query,
  5 docs each); first question on a fresh index may answer from partial evidence
  and say so in a `Limitations:` block.
- News coverage depends on the configured provider's history window; earnings-
  call transcripts are still not ingestible (spec schedules them with APEX).
- Per-agent traces nest under a service-level trace only conceptually — Langfuse
  shows them as separate traces today.
- No authentication or rate limiting — bind localhost/private networks only.
- Cost estimates use bundled public list prices; unknown models report no cost.

Production baseline audit: `docs/PRODUCTION_AUDIT.md`.
