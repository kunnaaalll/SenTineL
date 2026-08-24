# Sentinel — Agentic Financial Research Copilot
## Full Technical Specification

**Owner:** Kunal Parmar
**Status:** Pre-build — this document is the build spec for Claude Code
**Design principle:** Sentinel is a fully standalone system. It must run, deploy, and demo completely on its own, with zero dependency on any other project (including ApeXQuant/APEX ARIA). Any integration with external systems happens through an optional adapter that degrades gracefully if unavailable.

---

## 1. Product Overview

Sentinel is an agentic RAG platform for financial research. A user asks a natural-language question ("Compare Company X's debt load to its 3 closest competitors and flag risk factors"), and Sentinel:

1. Retrieves relevant SEC filings, earnings call transcripts, and market news
2. Extracts structured facts (numbers, dates, entities) from that content
3. For multi-entity/multi-period questions, cross-references facts across sources
4. Synthesizes a cited, natural-language answer

Simple single-fact questions skip the multi-agent path and go straight to retrieval + generation. Complex questions are routed through a LangGraph agent team.

Every LLM call — embedding, generation, each agent step — is traced in Langfuse for cost, latency, and quality monitoring.

---

## 2. Skill Coverage (why each piece exists)

| Skill | Where it lives |
|---|---|
| RAG | Core retrieval pipeline over filings/news/transcripts |
| LangChain | Ingestion chains, query rewriting |
| LangGraph | Multi-agent team: fetch → extract → compare → synthesize |
| Vector DB | Pinecone (cloud-hosted, supports standalone deployability) |
| FastAPI | Sentinel's own API layer |
| Prompt Engineering | Financial-domain extraction prompts, agent system prompts, query rewriting |
| Docker | Full standalone containerized stack |
| AWS/GCP | Independent deployment, separate from any other project's infra |
| Langfuse | Tracing on every LLM call across the whole pipeline |
| LoRA/QLoRA (optional stretch) | Fine-tuned financial entity extractor, only if time allows after core build |

---

## 3. High-Level Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      FRONTEND (Next.js)                        │
│   Chat interface + document/source browser + trace viewer link  │
└───────────────────────────┬──────────────────────────────────┘
                             │ REST (JSON over HTTPS)
┌───────────────────────────▼──────────────────────────────────┐
│                    BACKEND API (FastAPI)                       │
│   /ingest   /query   /agents/query   /sources   /providers      │
└───────────────────────────┬──────────────────────────────────┘
                             │
                ┌────────────┴─────────────┐
                ▼                           ▼
    ┌───────────────────────┐   ┌─────────────────────────┐
    │   Query Router          │   │   Ingestion Pipeline      │
    │  (simple vs multi-hop)  │   │  (source → chunk → embed) │
    └───────────┬────────────┘   └────────────┬─────────────┘
                │                              │
    ┌───────────▼────────────┐                 │
    │  Simple path:            │                │
    │  Naive RAG Chain         │                │
    └───────────┬────────────┘                 │
                │                              │
    ┌───────────▼────────────┐                 │
    │  Complex path:            │                │
    │  LangGraph Agent Team     │                │
    │  Fetch→Extract→Compare→   │                │
    │  Synthesize                │                │
    └───────────┬────────────┘                 │
                │                              │
                └──────────────┬───────────────┘
                               ▼
                  ┌─────────────────────────┐
                  │   Vector Store (Pinecone) │
                  └─────────────────────────┘
                               │
                  ┌────────────▼────────────┐
                  │   Data Source Interface   │
                  └────────────┬────────────┘
             ┌──────────────────┼──────────────────┐
             ▼                  ▼                    ▼
     ┌──────────────┐  ┌───────────────┐   ┌───────────────────┐
     │ SEC EDGAR      │  │ News API       │   │ APEX Adapter        │
     │ (always on)    │  │ (always on)    │   │ (OPTIONAL,            │
     │ Public/free    │  │                │   │ disabled by default,   │
     │                │  │                │   │ never a hard dep)       │
     └──────────────┘  └───────────────┘   └───────────────────┘

   All LLM calls (embedding, generation, agent steps) route through
   LLM Provider Engine → wrapped by Langfuse tracing.
```

---

## 4. Repository Structure

```
sentinel/
├── backend/
│   ├── api/
│   │   ├── main.py                 # FastAPI app, route registration
│   │   ├── routes_ingest.py
│   │   ├── routes_query.py
│   │   ├── routes_sources.py
│   │   └── routes_providers.py
│   ├── data_sources/
│   │   ├── base.py                 # DataSourceAdapter abstract interface
│   │   ├── sec_edgar.py
│   │   ├── news_api.py
│   │   └── apex_adapter.py         # optional, config-gated
│   ├── ingestion/
│   │   ├── financial_chunker.py    # table/footnote/MD&A-aware splitting
│   │   ├── entity_extractor.py     # tickers, dates, $ figures
│   │   └── pipeline.py             # orchestrates source→chunk→embed→store
│   ├── retrieval/
│   │   ├── base.py                 # VectorStore abstract interface
│   │   └── pinecone_store.py
│   ├── llm_providers/
│   │   ├── base.py                 # BaseProvider ABC (generate, embed, is_available)
│   │   ├── engine.py               # fallback-chain engine
│   │   ├── openai_provider.py
│   │   └── ollama_provider.py
│   ├── chains/
│   │   ├── rag_chain.py            # naive single-pass RAG (simple queries)
│   │   └── query_rewrite.py        # LangChain query reformulation
│   ├── agents/
│   │   ├── state.py                # shared LangGraph state schema
│   │   ├── graph.py                # LangGraph graph definition + router
│   │   ├── fetch_agent.py
│   │   ├── extract_agent.py
│   │   ├── compare_agent.py
│   │   └── synthesize_agent.py
│   ├── observability/
│   │   └── langfuse_wrapper.py     # decorator/context manager for tracing
│   ├── config/
│   │   ├── settings.py             # pydantic settings, env var loading
│   │   └── adapters.yaml           # which data sources are enabled
│   ├── models/
│   │   └── schemas.py              # shared Pydantic models (Document, Chunk, etc.)
│   ├── requirements.txt
│   └── tests/
│       ├── test_ingestion.py
│       ├── test_retrieval.py
│       ├── test_agents.py
│       └── test_api.py
│
├── frontend/
│   ├── app/
│   │   ├── page.tsx                 # main chat interface
│   │   ├── layout.tsx
│   │   └── sources/page.tsx         # browse ingested documents
│   ├── components/
│   │   ├── ChatWindow.tsx
│   │   ├── MessageBubble.tsx
│   │   ├── CitationCard.tsx         # shows source doc/page for a citation
│   │   ├── SourceUploadPanel.tsx
│   │   └── AgentTraceViewer.tsx     # shows which agents ran for a query
│   ├── lib/
│   │   └── api.ts                   # typed fetch wrappers for backend API
│   ├── package.json
│   ├── tsconfig.json
│   └── next.config.js
│
├── infra/
│   ├── Dockerfile.backend
│   ├── Dockerfile.frontend
│   ├── docker-compose.yml           # standalone stack — no external project deps
│   └── terraform/
│       ├── main.tf
│       ├── variables.tf
│       └── outputs.tf
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── API.md
│   └── AGENT_DESIGN.md
│
├── .env.example
├── README.md
└── Makefile
```

---

## 5. Data Models (Pydantic schemas — `backend/models/schemas.py`)

```python
class RawDocument(BaseModel):
    source_id: str  # e.g. "SEC:AAPL:10-K:2025"
    source_type: str  # "sec_filing" | "news" | "transcript" | "apex_portfolio"
    title: str
    published_date: date | None
    raw_text: str
    metadata: dict  # ticker, filing type, url, etc.


class Chunk(BaseModel):
    chunk_id: str
    source_id: str
    source_type: str
    section: str | None  # e.g. "MD&A", "Risk Factors", "Item 7A"
    page_or_position: str
    text: str
    entities: list[str]  # extracted tickers/dates/figures, if pre-extracted


class RetrievedChunk(Chunk):
    score: float


class AgentState(TypedDict):
    query: str
    query_type: Literal["simple", "multi_hop"]
    retrieved_chunks: list[RetrievedChunk]
    extracted_facts: list[dict]
    comparison_table: dict | None
    final_answer: str | None
    citations: list[dict]
    trace_id: str


class QueryResponse(BaseModel):
    answer: str
    citations: list[dict]  # {source_id, title, excerpt, url}
    agent_path: list[str]  # which agents ran, in order
    trace_url: str | None  # link to Langfuse trace
```

---

## 6. Data Source Interface

### 6.1 Abstract interface (`data_sources/base.py`)

```python
class DataSourceAdapter(ABC):
    name: str

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def fetch(self, query_params: dict) -> list[RawDocument]: ...
```

### 6.2 SEC EDGAR adapter
- Uses SEC's free public EDGAR full-text search + filing API
- `fetch()` accepts `{ticker, filing_type, date_range}`
- Always available — no API key required, just rate-limit-respecting headers (SEC requires a descriptive User-Agent string)

### 6.3 News API adapter
- Provider: **Financial Modeling Prep** (recommended default — has both news and fundamentals in one API, reasonable free tier). Alternative: AlphaVantage.
- `fetch()` accepts `{ticker, date_range, keywords}`
- Requires `NEWS_API_KEY`; `is_available()` returns `False` if key is missing/invalid — pipeline continues without it, doesn't crash

### 6.4 APEX Adapter (optional, disabled by default)
- Implements the same interface
- Calls APEX's exposed REST/gRPC endpoint only — **never** imports APEX's internal code
- Controlled by `config/adapters.yaml`: `apex.enabled: false` by default
- `is_available()` does a lightweight health check against `APEX_ENDPOINT_URL`; if unreachable, returns `False` and Sentinel proceeds without it
- **Build this adapter last, after everything else works standalone** — confirm APEX exposes a usable public endpoint before starting this piece; if not, this is deferred indefinitely without blocking the rest of Sentinel

### 6.5 `config/adapters.yaml`
```yaml
sec_edgar:
  enabled: true
news_api:
  enabled: true
  provider: financial_modeling_prep
apex:
  enabled: false
  endpoint: ${APEX_ENDPOINT_URL}
```

---

## 7. Ingestion Pipeline

1. `RawDocument` fetched from a data source
2. `financial_chunker.py` splits it — must handle three content shapes differently:
   - **Prose sections** (MD&A, business overview) → recursive character/sentence-boundary splitting, ~800 char chunks, 150 overlap
   - **Tables** (balance sheets, income statements) → kept as atomic chunks per table, never split mid-table; converted to a markdown-table string representation for embedding
   - **Footnotes** → attached as chunk metadata to the section they annotate, not embedded standalone
3. `entity_extractor.py` runs a lightweight pass (regex + LLM-assisted) to tag each chunk with tickers, dates, and dollar figures found in it — stored in `Chunk.entities` for filtering/boosting at retrieval time
4. Chunks embedded via `LLMEngine.embed()`
5. Stored in Pinecone with metadata: `source_id`, `source_type`, `section`, `ticker`, `date`

---

## 8. Vector Store (Pinecone)

### 8.1 Interface (`retrieval/base.py`)
Same abstract pattern as ingestion adapters:
```python
class VectorStore(ABC):
    @abstractmethod
    def add(self, chunks: list[Chunk], vectors: list[list[float]]) -> None: ...
    @abstractmethod
    def search(
        self, query_vector: list[float], top_k: int, filters: dict | None
    ) -> list[RetrievedChunk]: ...
```

### 8.2 Pinecone specifics
- Index dimension: matches embedding model (1536 for `text-embedding-3-small`)
- Metadata filtering supported at query time (filter by `ticker`, `source_type`, `date` range) — this matters a lot for financial queries ("only 2024 filings")
- Namespace per environment (`dev`, `prod`) to avoid cross-contamination during testing

---

## 9. LLM Provider Layer

Same pattern as your `axle` project's `BaseProvider`/`AIEngine`, ported and extended with `embed()`.

- `llm_providers/base.py` — `BaseProvider` ABC: `is_available()`, `generate()`, `embed()`
- `llm_providers/openai_provider.py` — primary provider (generation + embeddings)
- `llm_providers/ollama_provider.py` — free local fallback for dev/testing (chat only, no embeddings by default)
- `llm_providers/engine.py` — fallback chain: tries preferred provider, falls back down `PROVIDER_ORDER`

---

## 10. Agent Design (LangGraph)

### 10.1 Router logic
```
Incoming query
    │
    ▼
Classify query complexity (LLM call, cheap/fast model)
    │
    ├── "simple" (single entity, single fact) ──▶ Naive RAG Chain (chains/rag_chain.py)
    │
    └── "multi_hop" (multiple entities, comparison, ──▶ LangGraph Agent Team
         reasoning across sources)
```

### 10.2 Agent team detail

**Fetch Agent**
- Input: query + query classification
- Determines which entities (tickers), document types, and date ranges are relevant
- Calls the data source interface (retrieval from vector store if already ingested; triggers live ingestion via adapters if not)
- Output: candidate document/chunk set

**Extract Agent**
- Input: candidate chunks
- Pulls structured facts: specific metrics, dates, statements — one structured extraction per entity/topic
- Uses a strict extraction prompt (JSON schema output) to keep facts machine-comparable for the next agent
- Output: `list[dict]` of `{entity, metric, value, period, source_chunk_id}`

**Compare Agent**
- Only invoked when the query involves 2+ entities or time periods
- Aligns extracted facts into a comparison structure (e.g. debt-to-equity across 4 companies for the same fiscal year)
- Flags gaps (missing data for one entity) rather than silently omitting

**Synthesize Agent**
- Input: extracted facts + comparison table (if present)
- Produces the final natural-language answer with inline citations `[1]`, `[2]` mapped back to source chunks
- Enforces: no claim without a backing chunk; explicitly states when data is unavailable rather than inferring

### 10.3 State schema
Shared `AgentState` (TypedDict, see Section 5) threads through the whole graph — LangGraph nodes read/write specific keys, never the whole state blindly.

### 10.4 Prompts to design carefully
- Query classification prompt (simple vs multi-hop) — keep cheap/fast, this runs on every query
- Extraction prompt — must enforce structured JSON output reliably; use function-calling/tool-call style output if the provider supports it, not free-text parsing
- Synthesis prompt — enforces citation discipline and "say when you don't know"

---

## 11. Observability (Langfuse)

- `observability/langfuse_wrapper.py` provides a decorator (`@traced`) applied to:
  - Every LLM provider `generate()` and `embed()` call
  - Every agent node function
  - The top-level `/query` and `/agents/query` endpoint handlers
- Each trace captures: latency, token usage/cost, input/output, and — for agent runs — the full path taken (which agents fired, in what order)
- `QueryResponse.trace_url` returns a direct link to the Langfuse trace for that request, surfaced in the frontend's trace viewer

---

## 12. Backend API Specification

### `POST /ingest`
Request:
```json
{ "source_type": "sec_filing", "ticker": "AAPL", "filing_type": "10-K", "date_range": ["2024-01-01", "2024-12-31"] }
```
Response:
```json
{ "documents_ingested": 1, "chunks_indexed": 342 }
```

### `POST /query`
Request:
```json
{ "question": "What was Apple's revenue in fiscal 2024?" }
```
Response: `QueryResponse` (see schema in Section 5)

### `POST /agents/query`
Same request shape as `/query`, but always forces the multi-agent path regardless of classification — useful for testing/demoing the agent team directly.

### `GET /sources`
Response:
```json
{ "sec_edgar": true, "news_api": true, "apex": false }
```

### `GET /providers`
Response:
```json
{ "available": ["openai", "ollama"] }
```

---

## 13. Frontend Specification (Next.js)

### 13.1 Pages
- `/` — main chat interface: input box, message history, citation cards under each answer, "view agent trace" link per message
- `/sources` — browse/manage ingested documents; trigger new ingestion via a form (ticker, filing type, date range)

### 13.2 Key components
- `ChatWindow.tsx` — message list + input, calls `POST /query`
- `MessageBubble.tsx` — renders answer text with inline citation markers
- `CitationCard.tsx` — expandable card showing source doc title, excerpt, and link
- `AgentTraceViewer.tsx` — shows which agents fired for a given answer (fetch → extract → compare → synthesize), pulled from `QueryResponse.agent_path`
- `SourceUploadPanel.tsx` — form to trigger `/ingest`

### 13.3 State management
Simple React state / context is sufficient — no need for Redux/Zustand at this scale. Chat history can live in component state; no persistence layer needed for v1 (add later if multi-session history matters).

### 13.4 Styling
Tailwind CSS, minimal component library (or none) — this is a portfolio/demo project, not a production consumer app, so keep the frontend build lightweight and fast to ship.

---

## 14. Infrastructure & Deployment

### 14.1 Docker
- `Dockerfile.backend` — Python 3.11-slim base, installs `backend/requirements.txt`
- `Dockerfile.frontend` — Node-based multi-stage build for Next.js
- `docker-compose.yml` — brings up backend + frontend together; **no APEX service included**, since Sentinel must run fully standalone

### 14.2 Environment variables (`.env.example`)
```
OPENAI_API_KEY=
PINECONE_API_KEY=
PINECONE_ENVIRONMENT=
NEWS_API_KEY=
NEWS_API_PROVIDER=financial_modeling_prep
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com

# Optional — only needed if config/adapters.yaml enables apex
APEX_ENDPOINT_URL=
```

### 14.3 Deployment target
AWS (EC2 or ECS) or GCP Cloud Run — independent from wherever ApeXQuant is deployed; own Terraform state, own resources.

---

## 15. Testing Strategy
- `test_ingestion.py` — chunking correctness (tables stay atomic, prose splits sensibly), entity extraction accuracy on sample filings
- `test_retrieval.py` — Pinecone add/search round-trip, metadata filtering
- `test_agents.py` — each agent node in isolation with mocked LLM responses; full graph integration test with a known multi-hop question
- `test_api.py` — endpoint contract tests (request/response shape, error handling for missing sources)

---

## 16. Build Phases

| Phase | Deliverable |
|---|---|
| 1 | Data source interface + SEC EDGAR adapter + financial chunker + Pinecone store, backend only |
| 2 | LLM provider layer + naive single-pass RAG chain (`/query` working end-to-end for simple questions) |
| 3 | News API adapter + LangGraph agent team (fetch → extract → synthesize; compare added once basics validated) |
| 4 | Compare agent + multi-hop routing + Langfuse tracing wired through everything |
| 5 | Frontend (chat UI, citations, trace viewer) + Docker Compose + deploy |
| 6 (optional) | APEX adapter, only if APEX exposes a usable public endpoint by this point |

---

## 17. Explicit Non-Goals (for this build)
- No shared codebase or imports with ApeXQuant/APEX ARIA
- No requirement for ApeXQuant's infrastructure (Postgres/Redis/gRPC bus) to be running
- No trade decisioning of any kind — Sentinel is research/analysis only, same boundary APEX ARIA already follows for its own AI layer
- No multi-user auth/session system in v1 — single-user demo scope
- No persisted chat history in v1
