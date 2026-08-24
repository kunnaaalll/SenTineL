# Sentinel Architecture — Phase 3 state

Complements `SENTINEL_SPEC.md` (full target architecture) and
`docs/PRODUCTION_AUDIT.md` (Phase 0 baseline). This document describes what is
actually built after Phase 2: provider layer, observability, ingestion
pipeline, simple RAG path, and the API.

```
POST /ingest ──▶ IngestionPipeline ──▶ VectorStore (Pinecone)
                    │        ▲                ▲
                    │        │                │ search(add/delete)
     DataSourceAdapter       │                │
     (sec_edgar today)       │                │
                            LLMEngine ◀──────┘ embed()
                    ▲    (fallback chain)      ▲
                    │ traced by                 │
             LangfuseWrapper              POST /query ──▶ RagChain
                                                                 │ rewrite → embed → retrieve → generate → cite
```

## Import & configuration model

- `backend/` is a **source root** (no package `__init__` at that level);
  modules import absolutely (`from models.schemas import Chunk`). Tooling and
  uvicorn resolve imports with `backend/` on the path (`pyproject.toml`,
  `Makefile run`, CI).
- Everything degrades without credentials: no `OPENAI_API_KEY` → OpenAI provider
  reports unavailable; no Pinecone key → store reports not-ready; no langfuse
  package/keys → tracer is a no-op. Importing any module never requires an SDK
  or a key, and nothing makes network calls at import time.

## LLM provider layer (`backend/llm_providers/`)

- `base.py` — `BaseProvider` contract (`is_available`, `generate`, `embed`),
  typed results (`GenerationResult`, `EmbeddingResult`, `TokenUsage`), error
  taxonomy (`Transient`, `RateLimit`, `Authentication`, `InvalidRequest`,
  `Unavailable`), and static price tables for cost estimation.
- `openai_provider.py` — primary generation + embeddings. The SDK import is
  guarded; client construction is lazy and performs no I/O. SDK exceptions are
  classified into the taxonomy by duck-typed status inspection.
- `ollama_provider.py` — local fallback via plain HTTP to `OLLAMA_BASE_URL`
  (no extra SDK). Chat only unless `OLLAMA_EMBEDDING_MODEL` is set.
- `engine.py` — walks `LLM_PROVIDER_ORDER` (default `openai,ollama`):
  - skips unavailable providers (availability cached for
    `AVAILABILITY_TTL_SECONDS`);
  - retries transient failures / rate limits up to `LLM_MAX_RETRIES` with
    exponential backoff (`LLM_BACKOFF_BASE_SECONDS * 2^attempt` + jitter),
    honoring provider `Retry-After`;
  - **never retries** authentication errors or invalid-request errors;
    auth failures fall through to the next provider, invalid requests abort
    the whole chain (the payload would be rejected everywhere);
  - annotates every result with model, token usage, latency, and estimated cost.

## Observability (`backend/observability/langfuse_wrapper.py`)

One tiny interface: `Tracer.start_trace()` → `Trace` → `.span()` context
managers. Two implementations:

- `NullTracer` — default whenever Langfuse isn't configured; same interface,
  records nothing, `trace_url` stays null.
- `LangfuseTracer` — adapts to v2/v3 SDK shapes via feature detection; client
  injectable for tests.

Guarantees: metadata keys matching api_key/token/secret/password/
authorization/credential are redacted before export; oversized values are
truncated; span finish and trace creation never raise into the request path —
a broken tracer silently degrades to untraced operation.

Wired into: every engine attempt (`llm.generate` / `llm.embed` spans), the
ingestion pipeline (`ingest` trace), and each RAG query (`rag_query` trace —
its URL becomes `QueryResponse.trace_url`).

## Ingestion pipeline (`backend/ingestion/pipeline.py`)

1. Fetch through `DataSourceAdapter` (only `sec_edgar` registered in Phase 2).
   The SEC HTTP path self-throttles (~8 req/s) and applies bounded retries
   with backoff on transient statuses (408/429/5xx-class), honoring
   `Retry-After` (seconds or HTTP-date, clamped to 60s). Hard errors like 403
   bans fail immediately.
2. `financial_chunker` splits documents: prose ~800 chars / 150 overlap on
   sentence boundaries, tables atomic as markdown blocks, footnotes attached
   as chunk metadata.
3. `entity_extractor` tags tickers, dates, fiscal periods, dollar figures,
   percentages, and metric phrases — deterministic regex first. An optional
   LLM pass (`ENABLE_LLM_ENTITY_EXTRACTION=true`) only ever adds validated
   entries on top of the deterministic floor.
4. Embedding runs batched (`INGEST_BATCH_SIZE`) through the engine; dimension
   mismatches against the index fail loudly per document rather than storing
   incompatible vectors.
5. Storage goes through `VectorStore`. Metadata is capped below Pinecone's
   ~40KB per-vector limit (`fit_metadata`: drop footnotes → truncate text with
   an explicit marker); truncations are counted in stats.

**Idempotency:** chunk ids hash `(source_id, section, ordinal)` so unchanged
documents upsert over the same keys; before writing, the pipeline deletes all
existing vectors for the `source_id` (delete-before-reingest) so revised
filings can't leave orphaned chunks from shifted boundaries. Deletion happens
*after* embeddings succeed — a failed re-ingest never destroys the indexed copy.

**Failure semantics:** per-document isolation. One document failing is
recorded in `IngestionStats.failures` with its stage; remaining documents
continue; fetch-level failure short-circuits with a single `fetch` failure
entry. Stats always return.

## Query path (`backend/chains/`)

- `query_rewrite.py` — deterministic normalization (whitespace, filler
  phrases, `$cashtag` uppercasing) plus conservative ticker detection. A
  ticker filter is emitted only when exactly one ticker is detected —
  comparison questions must not pre-filter away half their evidence. Optional
  LLM rewrite mode behind `ENABLE_LLM_QUERY_REWRITE` falls back to the
  deterministic result on any failure.
- `rag_chain.py` — rewrite → embed → filtered retrieve → grounded context
  (numbered excerpts under a char budget) → generation with citation
  discipline → validated citations.

**Citation guarantees** (enforced in code, not just prompts):

1. Citations are parsed from `[n]` markers and validated against the actual
   retrieved set; out-of-range/duplicate markers are dropped.
2. Zero retrieved chunks → generation is skipped entirely; the answer states
   evidence is missing and suggests ingestion.
3. An `INSUFFICIENT_EVIDENCE` marker from the model clears citations and
   returns an explicit refusal plus the model's note about what's missing.
4. Every emitted citation maps 1:1 to a real retrieved chunk with provenance
   (source_id, title, excerpt, url, score).

## Agent team (`backend/agents/`) — Phase 3

Multi-hop questions and `/agents/query` execute a compiled LangGraph graph
over the typed `AgentState` contract:

```
classify (deterministic) ── simple ──► RagChain (untouched Phase 2 path)
        │ multi_hop / forced
        ▼
fetch ──► extract ──► [compare if facts span entities/periods] ──► synthesize
```

- `state.py` — `AgentState` TypedDict (spec section 5 keys + operational
  extensions: `agent_path`, `unavailable_sources`, `node_errors`,
  `ingested_keys`, `limitations`, `trace_urls`) and the strict
  `ExtractedFact` Pydantic model (`extra="forbid"`).
- `fetch_agent.py` — deterministic planner (tickers/years/source types) →
  indexed-first retrieval → budgeted live ingestion with loop protection;
  merges SEC + news evidence deduplicated by chunk id; unavailable sources are
  reported into state instead of raised.
- `extract_agent.py` — one strict json_mode call per chunk; server-forced
  provenance; conservative numeric parsing done locally, never by the model;
  per-chunk failure isolation plus a regex "deterministic floor" so downstream
  nodes always have grounded material.
- `compare_agent.py` — LLM-free alignment of facts into a
  `(metric, period) × entity` table; missing cells flagged, textual variants
  that parse to one number treated as consistent, mixed units noted.
- `synthesize_agent.py` — numbered-excerpt prompt → answer whose `[n]`
  citations are validated against real chunks exactly like the simple path;
  degradation ladder ends in a cited fact digest or an explicit refusal that
  names unavailable sources.
- `graph.py` — routing classifier, `_guarded()` node wrapper (one bounded
  retry then per-node degrade), conditional compare edge, acyclic topology
  with `recursion_limit=25` backstop, and `SentinelQueryService`, the single
  entry point both query routes call. Full behavior contract:
  `docs/AGENT_DESIGN.md`.

Each node is independently callable for tests; agents emit traces through the
injected Tracer abstraction (`agent_fetch`, `agent_extract`,
`agent_synthesize`, plus the service-level `agents_query`).

The news evidence itself comes from the Phase 3 `NewsApiAdapter`
(`backend/data_sources/news_api.py`): FMP-default provider registry,
ticker/date-range/keyword queries, bounded pagination, retry/backoff honoring
`Retry-After` (no retry on auth/invalid-request), deterministic dedup via URL/
content hash, sanitized text, and key-safe logging — mirroring the SEC
adapter's discipline.

## API (`backend/api/`)

`create_app()` wires components into `app.state` — everything injectable, so
the offline test suite drives the exact production wiring with fakes.
Endpoints: `POST /query` (auto-routing), `POST /agents/query` (forced agent
team), `POST /ingest`, `GET /sources`, `GET /providers`, `GET /health`,
`GET /ready`. Contracts documented in `docs/API.md`.

Error handling is centralized: one JSON envelope, validation details reduced
to loc/msg/type, internal exceptions logged server-side and returned as a
generic message. No secrets or stack traces cross the wire; agent-internal
diagnostics (`node_errors`, ingestion keys) never enter response bodies.

The APEX adapter remains unregistered and disabled (spec section 6.4) — it is
not part of any request path.

## Testing strategy

Fully offline: fake providers (scripted outcomes), an in-memory vector store
with source-delete awareness, canned adapters, and recording tracers. The
OpenAI SDK surface itself is faked at module boundary so the live code path is
exercised without the network. No test touches OpenAI, Ollama, Pinecone,
Langfuse, SEC, or news APIs; credential env vars are scrubbed per-test.

## Known limitations

- Routing is deterministic heuristics, not an LLM classifier; unusual phrasings
  may ride the simple path (`/agents/query` is the escape hatch).
- Per-agent traces appear as separate Langfuse traces rather than a nested
  hierarchy under one run trace.
- Metadata truncation (when it fires) reduces retrieval fidelity for affected
  giant-table chunks; the full text still exists upstream in EDGAR.
- Cost estimates rely on bundled public list prices; unknown models report
  `cost_usd = null` rather than guessing.
- Availability probes for Ollama touch loopback; everything else gates on
  static checks (package present + key present) and surfaces real auth issues
  at call time. News availability is key-presence-only — an expired key
  surfaces at first live call and is reported as an unavailable source.
- No auth/rate limiting (explicit v1 non-goal) — bind localhost/private only.

Deep dives: `docs/AGENT_DESIGN.md` (agent team), `docs/API.md` (endpoints),
`docs/PRODUCTION_AUDIT.md` (Phase 2 baseline).
