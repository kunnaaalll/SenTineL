# Sentinel

Agentic financial research copilot — RAG over SEC filings, earnings-call transcripts, and market news, producing cited natural-language answers. Simple questions go through a naive RAG chain; complex multi-entity questions go through a LangGraph agent team (Phase 3–4). Every LLM call is traced in Langfuse when configured.

Full architecture, API contracts, and build phases: see `SENTINEL_SPEC.md`. Phase 2 deep-dive: `docs/ARCHITECTURE.md`, `docs/API.md`. Phase 3 deep-dive: `docs/AGENT_DESIGN.md`.

## Status: Phase 5 (Production Next.js Frontend + Full Standalone Stack)

Latest milestone: Complete production-quality Next.js frontend milestone implemented with TypeScript, Tailwind CSS, typed API client with abort/timeout support, accessible chat UI with citations, agent trace viewer, and data source ingestion dashboard.

Implemented:

- **Frontend UI (`frontend/`)** — Next.js App Router (`/` and `/sources`), Tailwind CSS with light-first editorial financial research theme, terracotta & obsidian palette, `prefers-reduced-motion` support, full keyboard navigation (WCAG AA compliant).
  - `BackendGate` — conditional Render cold-start startup experience ("Namaste, welcome to Sentinel.") with bounded 120s readiness polling, live progress timer, friendly timeout retry, expandable sanitized details, and non-destructive session degradation.
  - `Sidebar` & `ConversationItem` — persistent 280px desktop sidebar with 56px collapsible rail, off-canvas mobile drawer with scrim, chronological session grouping (Today, Yesterday, Previous 7 days, Older), inline rename, and inline delete confirmation.
  - `ChatWindow` — browser-local multi-session management, sticky bottom composer with safe-area insets and scroll clearance, example queries, in-flight cancellation via Escape/button, and live region announcements.
  - `ResearchProcessingState` — restrained geometric research signal with thin ledger sweep and rotating verifiable research phases (zero spinners, bouncing dots, orbs, or fake percentages).
  - `MessageBubble` — markdown-safe answer rendering with GFM tables, inline interactive `[n]` citation markers, structured `Limitations:` caveat panels, explicit insufficient-evidence refusals.
  - `CitationCard` — expandable evidence cards detailing source title, excerpt, filing date, match score, section, and public EDGAR/news URLs.
  - `AgentTraceViewer` — collapsible multi-agent execution pipeline display (`classify → fetch → extract → compare → synthesize`) with Langfuse trace links.
  - `SourceUploadPanel` — SEC filing and market news ingestion forms with client-side validation, progress indicators, and detailed indexed chunk summaries.
  - `StatusBar` — live backend readiness indicator polling `/ready` with degraded/offline state handling.
  - `lib/useConversations.ts` — browser-local multi-session management under `sentinel:conversations` with automatic first-message title truncation, chronological grouping, corruption tolerance, and secret exclusion.
  - `lib/readiness.ts` — bounded polling state machine for backend cold-start detection.
  - `lib/api.ts` — typed client for all backend endpoints (`/query`, `/agents/query`, `/ingest`, `/sources`, `/providers`, `/health`, `/ready`) supporting timeouts, AbortSignal cancellation, safe error normalization, and runtime reverse-proxy routing via `BACKEND_ORIGIN`.
- **Frontend Containerization (`infra/Dockerfile.frontend`)** — Multi-stage standalone Next.js image running unprivileged (`USER node`, UID 1000), `/health` liveness probe.
- **Docker Compose Stack (`infra/docker-compose.yml`)** — Standalone dual-service stack (`sentinel-backend` + `sentinel-frontend`) attached to bridge network with loopback bindings (ports 8000 and 3000).
- **Backend & Core Engine (`backend/`)** — 393 offline unit tests, LangGraph agent team (`fetch → extract → compare → synthesize`), Financial Modeling Prep news adapter, SEC EDGAR adapter, fallback LLM engine, Pinecone vector store, and Langfuse tracing.

## Setup

### Backend

```bash
make setup            # creates .venv (Python 3.11) and installs backend/requirements-dev.txt
cp .env.example .env  # optional — an empty file is a valid offline configuration
make run              # starts FastAPI on http://127.0.0.1:8000
```

### Frontend

```bash
cd frontend
npm ci                # install locked dependencies from package-lock.json
npm run dev           # starts Next.js dev server on http://localhost:3000
```

### Docker quickstart (Full Stack)

The complete application (FastAPI backend + Next.js frontend) ships as hardened production containers:

```bash
# fully offline — no credentials needed to boot:
docker compose -f infra/docker-compose.yml up --build

# provider-enabled: put credentials in the gitignored .env first
cp .env.example .env   # fill in OPENAI_API_KEY / PINECONE_API_KEY / ...
docker compose -f infra/docker-compose.yml up --build
```

Access the UI at <http://127.0.0.1:3000> and the backend API at <http://127.0.0.1:8000>. Both publish on loopback by default. Detail: `docs/DEPLOYMENT.md`.

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

Both backend and frontend have strict quality gates:

```bash
# Full repository check
make check-all   # runs backend lint/typecheck/tests + frontend lint/typecheck/format/tests/build

# Backend gates
make fmt         # ruff format + safe lint autofixes
make lint        # ruff format --check + ruff check
make typecheck   # mypy
make test        # pytest offline suite (372 tests)

# Frontend gates (in frontend/)
npm run check    # typecheck + lint + format:check + vitest (71 tests)
npm run build    # Next.js standalone production build
```

CI (`.github/workflows/ci.yml`) runs independent jobs on every push/PR:
backend quality gates (ruff/mypy/pytest from the lockfile), frontend verification
(npm ci, typecheck, lint, prettier check, vitest unit tests, next build), production image
builds + contract checks (backend UID 10001, frontend UID 1000, no dev packages, import safety,
offline boot asserting `/health`=200 and `/ready`=503), the backend test suite
executed inside a Linux container, compose config validation, Terraform
fmt/init/validate, and gitleaks secret scanning. Pre-commit hooks
(`make hooks`) run the fast subset locally.

## Layout

Repository structure mirrors `SENTINEL_SPEC.md` section 4:

```
sentinel/
├── backend/                  # FastAPI service, pipelines, adapters, agent team
│   ├── agents/               # LangGraph agent team (fetch, extract, compare, synthesize)
│   ├── api/                  # FastAPI routes (/query, /agents/query, /ingest, /sources, /providers, /health, /ready)
│   ├── chains/               # Query rewriter, naive RAG chain
│   ├── config/               # Settings, adapter registry
│   ├── data_sources/         # SEC EDGAR and News API adapters
│   ├── ingestion/            # Financial chunker, entity extractor, pipeline
│   ├── llm_providers/        # Fallback engine, OpenAI & Ollama providers
│   ├── models/               # Shared Pydantic models (RawDocument, Chunk, Citation, QueryResponse)
│   ├── observability/        # Langfuse wrapper
│   ├── retrieval/            # Pinecone vector store
│   └── tests/                # 372 offline unit tests
│
├── frontend/                 # Next.js 16 + React 19 + Tailwind CSS v4 UI
│   ├── app/                  # App Router (/, /sources, /health, layout)
│   ├── components/           # ChatWindow, MessageBubble, CitationCard, AgentTraceViewer, SourceUploadPanel, StatusBar
│   ├── lib/api.ts            # Typed client for backend API with timeout/abort/error normalization
│   └── tests/                # 71 Vitest + Testing Library offline component tests
│
├── infra/
│   ├── Dockerfile.backend    # Multi-stage Python 3.11-slim production image (non-root 10001)
│   ├── Dockerfile.frontend   # Multi-stage Node 20-alpine Next.js standalone image (non-root node)
│   ├── docker-compose.yml    # Standalone stack (backend + frontend) on bridge network
│   └── terraform/            # Pinned AWS infrastructure skeleton
│
├── docs/                     # API.md, ARCHITECTURE.md, DEPLOYMENT.md, AGENT_DESIGN.md
└── Makefile
```

## Backend Readiness & Render Cold-Start Behavior

When hosted on containerized free-tier or serverless platforms such as Render, cold-starting backend instances take 50–90 seconds while spinning up. During this startup window, reverse proxies return `502 Bad Gateway`, `503 Service Unavailable`, `504 Gateway Timeout`, or drop connections.

Sentinel handles this through a non-blocking conditional gate (`BackendGate`):
1. **Silent Instant Passthrough**: On initial load, Sentinel immediately queries `/api/ready`. If the backend is healthy (`200 OK`), the full application interface renders instantly without splash screens or countdowns.
2. **Conditional Wake-Up Experience**: If the backend returns an error or times out, the user is greeted with a dedicated, cinematic welcome experience:
   - *"Namaste, welcome to Sentinel."*
   - *"The research engine is starting. This usually takes about one minute."*
   - Real-time elapsed timer and dynamic visual pulse indicator.
3. **Safe Bounded Polling**: Polls `/api/ready` with progressive backoff (2s → 5s interval, capped at 120s total).
4. **Graceful Timeout & Retry**: If 120 seconds elapse without backend response, the screen shifts to a calm retry prompt with an expandable technical diagnostics panel.
5. **Zero Leaks**: Stack traces, provider keys, raw backend payloads, and secret-bearing URLs are strictly scrubbed from user-facing states.
6. **Non-Destructive Session Degradation**: If the backend becomes unavailable *during* an active session, an unobtrusive degraded banner appears; existing conversation history and citations are never wiped.

## Local Browser Chat Persistence

Sentinel provides client-side conversation persistence without requiring accounts or cloud databases:
- **Versioned Key**: Saved under `sentinel.chat.v1` in `localStorage`.
- **Persisted Elements**: User questions, synthesized assistant responses, inline citations, agent execution paths, timestamps, and safe error states.
- **Hydration Safe**: State restoration is strictly deferred until client mount, preventing Next.js SSR hydration mismatches.
- **Bounded Capacity**: Fixed FIFO cap of 50 messages to prevent browser storage exhaustion.
- **Resilience**: Corrupted JSON, storage quota exceptions, and restricted storage (private browsing) are caught gracefully, falling back to memory-only state without crashing.
- **Clear Conversation**: Dedicated action with confirmation modal allows users to wipe local history at any time.
- **"Saved on this device"**: Subtle status indicator confirms local persistence status.

## Privacy & Security Limitations

- **No Remote User Data Persistence**: Chat conversations remain strictly on the user's browser device. No query history or user session cookies are written to the backend database.
- **Secret Hygiene**: Frontend storage excludes API keys, Authorization headers, Langfuse tokens, raw provider payloads, or sensitive environment configurations.
- **Browser-Scoped**: Clearing browser cookies/storage or switching devices/profiles starts a fresh conversation session.

## Known limitations

- Routing is deterministic heuristics (tickers/comparison words/periods), not
  an LLM classifier — unusual phrasings may ride the simple path; `/agents/query`
  is the explicit escape hatch.
- Live ingestion during agent queries is deliberately bounded (2 ingests/query,
  5 docs each); first question on a fresh index may answer from partial evidence
  and say so in a `Limitations:` block.
- News coverage depends on the configured provider's history window; earnings-
  call transcripts are not yet ingestible (spec schedules them with APEX).
- Per-agent traces nest under a service-level trace only conceptually — Langfuse
  shows them as separate traces today.
- No authentication or multi-user persistent session history — v1 scope is single-user demo/research.
- Cost estimates use bundled public list prices; unknown models report no cost.

Production baseline audit: `docs/PRODUCTION_AUDIT.md`.
