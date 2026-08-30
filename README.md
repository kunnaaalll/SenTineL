<div align="center">

# SENTINEL
### Autonomous Agentic Financial Intelligence Copilot

[![Tests](https://img.shields.io/badge/tests-533%20passed%20(100%25)-emerald?style=for-the-badge&logo=pytest)](https://github.com/kunnaaalll/SenTineL)
[![Frontend](https://img.shields.io/badge/next.js-16.3%20(App%20Router)-black?style=for-the-badge&logo=next.js)](https://github.com/kunnaaalll/SenTineL/tree/main/frontend)
[![Backend](https://img.shields.io/badge/fastapi-0.115%20(Python%203.11)-009688?style=for-the-badge&logo=fastapi)](https://github.com/kunnaaalll/SenTineL/tree/main/backend)
[![LangGraph](https://img.shields.io/badge/orchestration-langgraph%20multi--agent-orange?style=for-the-badge)](https://github.com/kunnaaalll/SenTineL/tree/main/backend/agents)
[![Vector DB](https://img.shields.io/badge/vector%20store-pinecone%20serverless-0052CC?style=for-the-badge)](https://www.pinecone.io/)
[![Tracing](https://img.shields.io/badge/observability-langfuse%20v2-blue?style=for-the-badge)](https://langfuse.com/)

<p align="center">
  <b>Grounded, cited, verifiable financial research across SEC filings, earnings calls, and real-time market news.</b><br>
  Eliminates hallucinations with deterministic fact alignment, multi-agent reasoning, and client-side privacy.
</p>

</div>

---

## Table of Contents

1. [Overview & Value Proposition](#1-overview--value-proposition)
2. [Key Capabilities & Innovations](#2-key-capabilities--innovations)
3. [System Architecture & Data Flow](#3-system-architecture--data-flow)
4. [Multi-Agent Graph (LangGraph)](#4-multi-agent-graph-langgraph)
5. [Directory & Project Structure](#5-directory--project-structure)
6. [Design System & Frontend Architecture](#6-design-system--frontend-architecture)
7. [Local Browser Multi-Session Chat](#7-local-browser-multi-session-chat)
8. [Backend Readiness & Render Cold-Start Protocol](#8-backend-readiness--render-cold-start-protocol)
9. [Getting Started & Local Development](#9-getting-started--local-development)
10. [Configuration & Environment Variables](#10-configuration--environment-variables)
11. [REST API Specification & Examples](#11-rest-api-specification--examples)
12. [Quality Gates & Automated Verification](#12-quality-gates--automated-verification)
13. [Production Deployment & Infrastructure](#13-production-deployment--infrastructure)
14. [Security, Privacy, and Data Governance](#14-security-privacy-and-data-governance)
15. [Documentation Index](#15-documentation-index)

---

## 1. Overview & Value Proposition

Financial analysts, institutional researchers, and portfolio managers spend hours sifting through dense 10-K, 10-Q, and 8-K filings to compare metrics across companies. Traditional LLMs hallucinate numbers, confuse fiscal calendars, and miss critical reporting footnotes.

**Sentinel** is an autonomous financial research copilot engineered to produce **fully grounded, cited, natural-language answers** with mathematical consistency:

- **Dual Retrieval Pipeline**: Simple questions route through a fast, single-hop Naive RAG chain. Multi-entity, multi-period comparative questions route through a dedicated **LangGraph Agent Team**.
- **Deterministic Fact Alignment**: Instead of asking an LLM to compare numbers directly in prose, Sentinel extracts structured facts (`entity`, `metric`, `period`, `numeric_value`, `unit`, `confidence`) and builds a strict matrix alignment table (`compare_agent`). Gaps and non-comparable accounting metrics are explicitly flagged.
- **Traceable Reasoning**: Every generated claim links directly to primary sources via interactive `[n]` citation badges with filing dates, document sections, match confidence, and public EDGAR/news URLs.
- **Client-Side Privacy**: Conversation transcripts, titles, and histories reside entirely inside the client browser's `localStorage` (`sentinel:conversations`). No user queries or chat logs are stored on backend databases.

---

## 2. Key Capabilities & Innovations

- **Interactive Evidence Badges (`[n]`)**: Clickable citation numbers embedded directly in Markdown answers that expand verified source cards showing exact excerpts, filing dates, and SEC links.
- **Structured Limitations Callouts**: If companies use non-comparable definitions (e.g., Microsoft Cloud vs. Google Cloud) or lack 1-to-1 matching disclosures, Sentinel flags them in dedicated caveat panels.
- **Dark Engineered Canvas**: An xAI/Grok-inspired user interface featuring `#0A0A0A` canvas, `#141518` card surfaces, 1px crisp hairline borders (`#212327`), sunset orange accents (`#FF7A17`), and high-legibility typography.
- **Permanently Docked Composer**: The question composer is locked at the bottom of the screen (`shrink-0`), allowing the message feed to scroll independently without lifting or shifting the input card.
- **Fixed Top Navigation Bar**: Top navigation bar stays docked at `h-14` with live backend health pills (`• Ready` / `• Degraded` / `• Offline`).
- **Multi-Session Chat History**: Full session management with automatic first-question titling (truncated to 48 characters on word boundaries), chronological grouping (**Today**, **Yesterday**, **Previous 7 days**, **Older**), inline renaming, and safe deletion.
- **Cold-Start Resilience (`BackendGate`)**: On scale-to-zero serverless platforms (such as Render), Sentinel displays a calm startup screen (*"Namaste, welcome to Sentinel. The research engine is starting."*) with bounded 120s readiness polling and zero credential leakage.

---

## 3. System Architecture & Data Flow

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                   NEXT.JS 16 FRONTEND                                  │
│  App Shell • Fixed Top Nav • Multi-Session Sidebar • Message Stream • Permanent Dock   │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │ REST API (JSON / HTTP proxy)
┌───────────────────────────────────────────▼────────────────────────────────────────────┐
│                                    FASTAPI BACKEND                                     │
│  /query  •  /agents/query  •  /ingest  •  /sources  •  /providers  •  /health  • /ready│
└─────────────────────┬─────────────────────────────────────────────────┬────────────────┘
                      │                                                 │
          ┌───────────▼────────────┐                       ┌────────────▼───────────┐
          │     Query Router       │                       │   Ingestion Pipeline   │
          │ (Single vs Multi-Hop)  │                       │ (Chunker • Extractor)  │
          └─────┬────────────┬─────┘                       └────────────┬───────────┘
                │            │                                          │
    ┌───────────▼───┐    ┌───▼────────────────────────┐                 │
    │  Simple Path  │    │      Complex Path          │                 │
    │   Naive RAG   │    │  LangGraph Agent Team      │                 │
    │  (Single Hop) │    │  Fetch→Extract→Compare→    │                 │
    │               │    │  Synthesize                │                 │
    └───────────┬───┘    └───┬────────────────────────┘                 │
                │            │                                          │
                └────────────┼──────────────────────────────────────────┘
                             ▼
              ┌─────────────────────────────┐
              │   Vector Store (Pinecone)   │
              │ (Serverless Cosine Search)  │
              └──────────────┬──────────────┘
                             │
            ┌────────────────┴────────────────┐
            ▼                                 ▼
┌───────────────────────┐         ┌───────────────────────┐
│     SEC EDGAR API     │         │   Market News Feed    │
│ (10-K, 10-Q, 8-K, MD&A│         │ (FMP News Provider)   │
└───────────────────────┘         └───────────────────────┘
```

---

## 4. Multi-Agent Graph (LangGraph)

For complex multi-entity, comparative, or cross-period queries, Sentinel executes a stateful graph orchestrated via LangGraph:

```
[START] ──► [classify] ──► [fetch] ──► [extract] ──► (comparison_warranted?)
                                                          │          │
                                                    YES   │          │ NO
                                                          ▼          │
                                                     [compare]       │
                                                          │          │
                                                          ▼          ▼
                                                     [synthesize] ◄──┘
                                                          │
                                                          ▼
                                                       [END]
```

### Agent Roles:
1. **`classify`**: Parses the incoming question, determines entity targets (e.g. `["AAPL", "MSFT"]`), target reporting periods, and decides whether multi-agent comparison is warranted.
2. **`fetch`**: Performs targeted multi-vector retrieval across SEC filings and market news chunks for each identified entity.
3. **`extract`**: Extracts deterministic financial facts (`ExtractedFact`) from raw excerpts, normalizing metric names, monetary units (millions vs billions vs percentage), and reporting periods.
4. **`compare`**: Evaluates extracted facts across entities and periods. Generates an alignment matrix, marks conflicts (different figures for the same metric/period), and counts `missing` cells where disclosures diverge.
5. **`synthesize`**: Consolidates verified facts, comparison tables, and source excerpts into a structured natural-language answer with strict citation markers (`[1]`, `[2]`), followed by an automated limitations disclosure.

---

## 5. Directory & Project Structure

```
SenTineL/
├── Makefile                          # Unified build, test, lint, and verification targets
├── pyproject.toml                    # Python package definition, ruff, mypy, pytest configs
├── README.md                         # Comprehensive project documentation
├── CHANGELOG.md                      # Release changelog and milestone tracking
├── SENTINEL_SPEC.md                  # Master technical specification
│
├── backend/                          # FastAPI Backend Application (Source Root)
│   ├── agents/                       # LangGraph multi-agent team
│   │   ├── classify_agent.py         # Entity & intent classifier
│   │   ├── fetch_agent.py            # Targeted multi-source retrieval agent
│   │   ├── extract_agent.py          # Financial metric & entity extractor
│   │   ├── compare_agent.py          # Deterministic fact alignment & comparison matrix
│   │   ├── synthesize_agent.py       # Final cited answer synthesis agent
│   │   ├── graph.py                  # LangGraph StateGraph assembly & conditional routing
│   │   └── state.py                  # Typed AgentState and ExtractedFact models
│   ├── api/                          # FastAPI route handlers & middleware
│   │   ├── main.py                   # FastAPI app entrypoint, CORS, route registry
│   │   └── routes.py                 # /query, /agents/query, /ingest, /sources, /ready, etc.
│   ├── chains/                       # Naive RAG chains and query rewriting
│   │   ├── query_rewriter.py         # LLM & heuristic query expansion
│   │   └── rag_chain.py              # Single-hop retrieval-augmented generation
│   ├── config/                       # Settings management & provider registries
│   │   ├── settings.py               # Pydantic Settings with SecretStr guards
│   │   └── adapter_registry.py       # Data source & provider registries
│   ├── data_sources/                 # Financial document adapters
│   │   ├── sec_edgar.py              # SEC EDGAR downloader & fair-access compliant client
│   │   └── news_api.py               # Financial Modeling Prep news adapter
│   ├── ingestion/                    # Financial document chunking & parsing
│   │   ├── chunker.py                # Item/section aware financial chunker
│   │   ├── entity_extractor.py       # Regex & LLM-based entity tagger
│   │   └── pipeline.py               # Ingestion orchestrator
│   ├── llm_providers/                # Resilient multi-provider fallback engine
│   │   ├── engine.py                 # Provider fallback executor with backoff & caching
│   │   ├── openai_provider.py        # OpenAI API provider (generation & embedding)
│   │   └── ollama_provider.py        # Local Ollama offline provider
│   ├── models/                       # Shared Pydantic data schemas
│   │   └── schemas.py                # RawDocument, Chunk, Citation, QueryResponse, etc.
│   ├── observability/                # Tracing & telemetry
│   │   └── tracer.py                 # Langfuse v2 wrapper & null tracer fallback
│   ├── retrieval/                    # Vector database integrations
│   │   └── vector_store.py           # Pinecone vector store wrapper with namespacing
│   ├── requirements.txt              # Core runtime dependencies
│   ├── requirements-dev.txt          # Development, testing, and linting tools
│   ├── requirements-lock.txt         # Fully pinned dependency lockfile
│   └── tests/                        # 393 Offline Backend Unit & Integration Tests
│
├── frontend/                         # Next.js 16 App Router Frontend
│   ├── app/                          # Next.js App Router
│   │   ├── globals.css               # Dark Engineered Canvas design tokens & Tailwind theme
│   │   ├── icon.svg                  # Flat geometric Sentinel S-mark favicon
│   │   ├── layout.tsx                # Root layout wrapping BackendGate & AppShell
│   │   ├── page.tsx                  # Primary Research chat page (/)
│   │   ├── sources/page.tsx          # Data sources & ingestion dashboard (/sources)
│   │   └── health/route.ts           # Frontend liveness probe (/health)
│   ├── components/                   # Reusable React 19 UI Components
│   │   ├── AppShell.tsx              # Application layout shell, fixed header, sidebar context
│   │   ├── Sidebar.tsx               # 280px sidebar, 56px rail, and mobile drawer
│   │   ├── ConversationItem.tsx      # Session item with inline rename & delete confirmation
│   │   ├── ChatWindow.tsx            # Isolated message stream & permanently docked composer
│   │   ├── MessageBubble.tsx         # User bubbles, assistant cards with left sunset border
│   │   ├── AnswerMarkdown.tsx        # GFM tables, Markdown parser, interactive [n] badges
│   │   ├── CitationCard.tsx          # Expandable source evidence cards
│   │   ├── ResearchProcessingState.tsx # Geometric ledger signal sweep animation
│   │   ├── AgentTraceViewer.tsx      # Collapsible agent execution path visualizer
│   │   ├── SourceUploadPanel.tsx     # SEC filing & news ingestion forms
│   │   ├── StatusBar.tsx             # Live backend readiness indicator
│   │   ├── BackendGate.tsx           # Render cold-start welcome screen & bounded polling
│   │   └── SentinelLogo.tsx          # Code-native accessible SVG logo variants
│   ├── lib/                          # Client utilities and hooks
│   │   ├── api.ts                    # Typed API client with timeout & AbortSignal cancellation
│   │   ├── persistence.ts            # Local storage helper & validation functions
│   │   ├── readiness.ts              # Bounded exponential-backoff polling engine
│   │   ├── useConversations.ts       # Multi-session localStorage manager (sentinel:conversations)
│   │   └── ConversationsContext.tsx  # React context for cross-component session sync
│   ├── package.json                  # Next.js 16, React 19, Tailwind CSS v4, Vitest
│   └── tests/                        # 140 Offline Vitest Component & Unit Tests
│
├── infra/                            # Infrastructure & Deployment Automation
│   ├── Dockerfile.backend            # Hardened multi-stage Python 3.11 image (non-root 10001)
│   ├── Dockerfile.frontend           # Hardened multi-stage Node 20-alpine image (non-root node)
│   ├── docker-compose.yml            # Standalone dual-service local development stack
│   ├── bootstrap/                    # Staging bootstrap scripts
│   └── terraform/                    # AWS Terraform Staging Infrastructure
│       ├── main.tf                   # Providers & backend configuration
│       ├── networking.tf             # VPC, public/private subnets, NAT Gateway, Endpoints
│       ├── security_groups.tf        # Least-privilege security group matrix
│       ├── alb.tf                    # Application Load Balancer with path-based routing
│       ├── ecs.tf                    # ECS Fargate cluster, tasks, and services
│       ├── iam.tf                    # Task Execution & Task IAM roles
│       ├── secrets.tf                # AWS Secrets Manager resource definitions
│       └── cloudwatch.tf             # Log groups, metric alarms, and SNS topics
│
└── docs/                             # Deep-Dive Engineering Documentation
    ├── ARCHITECTURE.md               # System architecture and retrieval specifications
    ├── AGENT_DESIGN.md               # LangGraph multi-agent team technical deep dive
    ├── API.md                        # Complete REST API specification
    ├── BRAND_GUIDELINES.md           # Brand identity, typography, and color tokens
    ├── DEPLOYMENT.md                 # Deployment runbooks, container specs, and Terraform
    ├── OPERATIONS.md                 # Runbooks, monitoring, and troubleshooting
    ├── SECURITY.md                   # Threat modeling and data boundary guarantees
    ├── RELEASE_CHECKLIST.md          # Pre-flight release validation checklist
    ├── GO_LIVE_RUNBOOK.md            # Step-by-step production rollout procedure
    ├── STAGING_BOOTSTRAP.md          # Terraform staging environment bootstrap guide
    ├── STAGING_HANDOFF.md            # Infrastructure handoff and verification summary
    └── specs/                        # Historical and feature specifications
        ├── CHAT_SESSIONS_SPEC.md     # Browser-local chat session specification
        └── DESIGN_V4_ARCHIVE.md      # Design system archive document
```

---

## 6. Design System & Frontend Architecture

Sentinel employs a **Dark Engineered Canvas** design language:

### 1. Palette & Surface Tokens
- **Background Canvas (`--background`)**: `#0A0A0A` near-black foundation.
- **Card Surfaces (`--surface`)**: `#141518` / `#16171A` with 1px solid hairline borders (`#212327`).
- **User Bubbles (`--surface-raised`)**: `#1C1D22` dark bubble with crisp `#FFFFFF` text.
- **Assistant Cards**: `#141518` container featuring a signature 3px left sunset border (`border-l-2 border-[#FF7A17]`).
- **Sunset Accent (`--accent`)**: `#FF7A17` / `#FF9E4F` used for action buttons, citation badges, and active session indicators.
- **Status Indicator (`--success`)**: `#10B981` emerald green indicator dot.

### 2. Viewport & Layout Architecture
- **Fixed Top Navigation Bar**: Stays docked at `h-14` with zero layout shift during scrolling.
- **Dedicated Message Scroll Viewport**: `<main>` isolates scrolling strictly to the message feed (`flex-1 overflow-y-auto min-h-0`).
- **Permanently Docked Composer**: Positioned as a `shrink-0` bottom element below the message container. It **never gets lifted or shifted** into the middle of the screen when scrolling.

---

## 7. Local Browser Multi-Session Chat

Sentinel provides rich, client-side conversation sessions without requiring user logins or storing chat transcripts on a server:

```json
{
  "version": 1,
  "updatedAt": "2026-08-30T17:50:00.000Z",
  "conversations": [
    {
      "id": "conv-1725021000000-a1b2c3d4",
      "title": "Apple FY24 Total Net Sales",
      "titleIsCustom": false,
      "createdAt": "2026-08-30T17:00:00.000Z",
      "updatedAt": "2026-08-30T17:05:00.000Z",
      "messages": [
        {
          "id": "m1",
          "role": "user",
          "question": "What was Apple's total net sales in fiscal 2024?",
          "status": "complete"
        },
        {
          "id": "m2",
          "role": "assistant",
          "question": "What was Apple's total net sales in fiscal 2024?",
          "status": "complete",
          "answer": "Apple reported total net sales of $391.0 billion in fiscal 2024 [1].",
          "citations": [...]
        }
      ]
    }
  ]
}
```

- **Storage Key**: `sentinel:conversations` in `localStorage` (with backward compatibility for `sentinel.chat.v1`).
- **Automatic Titling**: Automatically generated from the first question, cleanly truncated at word boundaries (up to 48 characters).
- **Chronological Grouping**: Automatically organized into **Today**, **Yesterday**, **Previous 7 days**, and **Older**.
- **Full CRUD & Inline Editing**: Create new chat, switch active session, inline rename (`Enter` to save, `Escape` to cancel), and inline delete confirmation without native popup prompts.
- **Safety Guards**: In-flight queries (`status: "pending"`) and API keys are never persisted. Bounded FIFO capacity (max 50 sessions, 100 messages each) protects against storage exhaustion.

---

## 8. Backend Readiness & Render Cold-Start Protocol

When hosted on scale-to-zero serverless platforms (such as Render free/hobby tiers), backend spin-up takes 50–90 seconds. During this window, reverse proxies return `502 Bad Gateway`, `503 Service Unavailable`, or `504 Gateway Timeout`.

Sentinel solves this with a non-blocking conditional gate (`BackendGate`):

1. **Immediate Passthrough**: On load, Sentinel queries `/api/ready`. If the backend responds `200 OK`, the application renders instantly with zero splash screens or delays.
2. **Graceful Startup Screen**: If the backend is starting up or unreachable, the user is greeted with a calm welcome screen:
   - *"Namaste, welcome to Sentinel."*
   - *"The research engine is starting. This usually takes about one minute."*
   - Live elapsed timer and progress indicator.
3. **Bounded Safe Polling**: Polls `/api/ready` with progressive backoff (2s → 5s), strictly capped at 120 seconds.
4. **Friendly Timeout & Retry**: If 120 seconds elapse without response, polling stops to prevent infinite loops, offering a manual Retry button and a sanitized technical diagnostics panel.
5. **Zero Secret Leakage**: Raw backend error payloads, database paths, and API keys are strictly masked from user-facing error states.

---

## 9. Getting Started & Local Development

### Prerequisites
- **Python**: 3.11+
- **Node.js**: 20.9.0+ and `npm`
- **Docker**: (Optional, for containerized execution)

### 1. Backend Setup

```bash
# 1. Create Python 3.11 virtual environment and install dependencies
make setup

# 2. Configure environment (an empty .env is valid for offline mode)
cp .env.example .env

# 3. Start the FastAPI backend server
make run
# Backend is available at http://127.0.0.1:8000 (Swagger docs at /docs)
```

### 2. Frontend Setup

```bash
# 1. Navigate to the frontend directory
cd frontend

# 2. Install dependencies
npm ci

# 3. Start the Next.js development server
npm run dev
# Frontend is available at http://localhost:3000
```

### 3. Full-Stack Docker Quickstart

To run the complete production stack (FastAPI backend + Next.js frontend) in isolated containers:

```bash
# Boot the entire stack on loopback ports 8000 and 3000
docker compose -f infra/docker-compose.yml up --build
```

Access the UI at `http://127.0.0.1:3000` and the API at `http://127.0.0.1:8000`.

---

## 10. Configuration & Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | *(unset)* | OpenAI API key for generation and embeddings |
| `PINECONE_API_KEY` | *(unset)* | Pinecone API key for vector retrieval |
| `PINECONE_INDEX_NAME` | `sentinel` | Target Pinecone vector index name |
| `NEWS_API_KEY` | *(unset)* | Financial Modeling Prep news API key |
| `SEC_CONTACT_EMAIL` | `placeholder@example.com` | User-Agent contact for SEC EDGAR compliance |
| `SENTINEL_ENV` | `dev` | Environment namespace (`dev` vs `prod` index isolation) |
| `LLM_PROVIDER_ORDER` | `openai,ollama` | Ordered fallback sequence for LLM calls |
| `OPENAI_GENERATION_MODEL`| `gpt-4o-mini` | Default model for synthesis and query rewriting |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small`| Default model for vector embeddings |
| `OLLAMA_BASE_URL` | `http://localhost:11434`| Local Ollama service URL for offline fallback |
| `OLLAMA_GENERATION_MODEL`| `llama3.1` | Local Ollama model for generation |
| `LANGFUSE_PUBLIC_KEY` | *(unset)* | Langfuse public tracing key |
| `LANGFUSE_SECRET_KEY` | *(unset)* | Langfuse secret tracing key |
| `LANGFUSE_HOST` | `https://cloud.langfuse.com` | Langfuse tracing endpoint |
| `RAG_TOP_K` | `6` | Maximum vector chunks retrieved per search |
| `RAG_EXCERPT_CHARS` | `1600` | Character limit per retrieved chunk excerpt |
| `RAG_CONTEXT_CHAR_BUDGET`| `9000` | Total context token budget for synthesis prompt |

---

## 11. REST API Specification & Examples

### Endpoints Overview

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/query` | Simple question RAG query (naive single-hop path) |
| `POST` | `/agents/query` | Complex question query (LangGraph multi-agent team) |
| `POST` | `/ingest` | Ingest SEC filings or market news into vector store |
| `GET` | `/sources` | Check availability of SEC EDGAR, News API, and APEX adapters |
| `GET` | `/providers` | Check status of LLM generation and embedding providers |
| `GET` | `/ready` | Deep readiness probe (verifies database & provider readiness) |
| `GET` | `/health` | Lightweight liveness probe |
| `GET` | `/metrics` | Prometheus operational metrics |

### Example Query Request

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What was Apple total net sales in fiscal 2024?"
  }'
```

### Example Response

```json
{
  "question": "What was Apple total net sales in fiscal 2024?",
  "answer": "According to Apple's fiscal 2024 Form 10-K, total net sales were $391.04 billion, compared to $383.29 billion in fiscal 2023 [1].",
  "citations": [
    {
      "source_id": "SEC:AAPL:10-K:2024-11-01",
      "title": "Apple Inc. 10-K filed 2024-11-01",
      "chunk_id": "chunk_042",
      "section": "Item 7 - Management's Discussion and Analysis",
      "score": 0.892,
      "excerpt": "Total net sales were $391,035 million in 2024 compared to $383,285 million in 2023...",
      "url": "https://www.sec.gov/ix?doc=/Archives/edgar/data/320193/000032019324000106/aapl-20240928.htm"
    }
  ],
  "agent_path": ["classify", "naive_rag"],
  "trace_url": "https://cloud.langfuse.com/project/..."
}
```

---

## 12. Quality Gates & Automated Verification

Sentinel enforces 100% automated test coverage across both backend and frontend. All test suites run completely offline with mocked providers:

```bash
# Run all quality gates across the entire repository
make check-all

# Backend Quality Gates
make fmt          # Auto-format Python code (ruff format + safe lint fixes)
make lint         # Lint checks (ruff format --check + ruff check)
make typecheck    # Strict static type check (mypy)
make test         # Run 393 offline pytest tests

# Frontend Quality Gates (in frontend/)
npm run check     # Runs tsc + eslint + prettier:check + vitest (140 tests)
npm run build     # Production Next.js build
```

### Test Statistics
- **Backend Tests (`pytest`)**: **393 / 393 passed** (100% offline, zero network calls).
- **Frontend Tests (`vitest`)**: **140 / 140 passed** (13 test suites, zero warnings).
- **TypeScript**: `tsc --noEmit` **0 errors**.
- **ESLint & Prettier**: **0 errors, 0 warnings, 100% format compliance**.

---

## 13. Production Deployment & Infrastructure

### 1. Hardened Container Images
- **Backend (`infra/Dockerfile.backend`)**: Multi-stage `python:3.11-slim-trixie` image running unprivileged as `sentinel` (UID 10001), read-only root filesystem, tmpfs `/tmp`, `cap_drop: ALL`.
- **Frontend (`infra/Dockerfile.frontend`)**: Multi-stage `node:20-alpine` standalone Next.js server running as `node` (UID 1000).

### 2. AWS Staging Infrastructure (`infra/terraform/`)
Codified in Terraform targeting AWS ECS Fargate with zero public database exposure:
- **Networking**: Multi-AZ VPC (`10.0.0.0/16`), public & private subnets, NAT Gateway, VPC Endpoints for S3, ECR, Secrets Manager, and CloudWatch.
- **Compute**: ECS Fargate cluster with Container Insights.
- **Load Balancing**: Application Load Balancer with path-based routing (`/*` to frontend, `/api/*`, `/query`, `/docs` to backend).
- **Secrets Management**: AWS Secrets Manager with KMS encryption.
- **Observability**: CloudWatch log groups and metric alarms for CPU, Memory, 5XX errors, and target latency.

---

## 14. Security, Privacy, and Data Governance

1. **Zero Secret Leakage**: API keys, Bearer tokens, and secrets are typed as Pydantic `SecretStr` on the backend and never exposed in frontend bundles or logged to consoles.
2. **Client-Side Data Boundary**: All chat histories and session titles live purely inside the user's browser `localStorage`. No chat queries or prompts are recorded on the backend database.
3. **SEC Fair-Access Compliance**: The SEC EDGAR client enforces rate limits and custom User-Agent formatting (`Sample Company Name AdminContact@<sample company domain>.com`) to respect SEC fair-access policies.
4. **Anti-Hallucination Guardrails**: If no indexed evidence exists in Pinecone, Sentinel returns an explicit refusal rather than guessing.

---

## 15. Documentation Index

For in-depth architectural and operational guides, refer to the `docs/` catalogue:

- 🏛️ **[System Architecture](docs/ARCHITECTURE.md)**: Deep dive into chunking, retrieval budgets, and vector search.
- 🤖 **[Agent Design](docs/AGENT_DESIGN.md)**: Detailed LangGraph state graph, node transitions, and comparison algorithms.
- 🔌 **[API Documentation](docs/API.md)**: Complete REST API request/response schemas.
- 🎨 **[Brand Guidelines](docs/BRAND_GUIDELINES.md)**: Design system tokens, logo geometry, and WCAG contrast tables.
- 🚀 **[Deployment & Operations](docs/DEPLOYMENT.md)**: Containerization, Docker Compose, and Terraform staging setup.
- 🛡️ **[Security Architecture](docs/SECURITY.md)**: Threat modeling, boundary controls, and credentials policy.
- 📋 **[Release Checklist](docs/RELEASE_CHECKLIST.md)**: Production release verification gates.
- 📖 **[Go-Live Runbook](docs/GO_LIVE_RUNBOOK.md)**: Step-by-step production rollout procedure.
- ⚙️ **[Staging Bootstrap](docs/STAGING_BOOTSTRAP.md)**: Terraform remote state and AWS bootstrap guide.
- 📑 **[Specs Archive](docs/specs/)**: Historical and feature specifications.

---

<div align="center">
  <b>Sentinel Financial Intelligence</b> • Built with precision for grounded financial research.
</div>
