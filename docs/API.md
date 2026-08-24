# Sentinel API

FastAPI service exposing ingestion, the routed query paths (simple RAG vs
multi-agent), and provider/source status.

> **PRIVATE / LOCAL-ONLY.** There is no authentication in v1 by design
> (`SENTINEL_SPEC.md` section 17). Never expose this service beyond
> localhost or a private network. Run with:
>
> ```bash
> make run   # uvicorn api.main:app --host 127.0.0.1 --port 8000
> ```
>
> Interactive docs: <http://127.0.0.1:8000/docs>

All request and response bodies are JSON. Every error — validation, upstream,
or internal — uses one envelope:

```json
{ "error": { "code": "machine_readable_code", "message": "human summary", "details": "optional" } }
```

`details` on 422 responses contains only `loc` / `msg` / `type` per issue — raw
input values are never echoed back. 500 responses carry a generic message;
tracebacks stay in server logs.

---

## POST /query

Single question-answering entry point with **automatic complexity routing**
(Phase 3): a deterministic classifier inspects the question first.

- **simple** — one entity, no comparison vocabulary, at most one period → the
  proven Phase 2 RAG chain answers (`rewrite → embed → retrieve → generate`).
- **multi_hop** — two or more tickers, comparison vocabulary ("compare",
  "versus", "year-over-year", …), or two or more period tokens → the agent team
  runs (`fetch → extract → [compare] → synthesize`, see `docs/AGENT_DESIGN.md`).

The request/response contract is identical on both branches; `agent_path`
always starts with `classify` and shows which path executed.

**Request**

| Field | Type | Constraints | Notes |
|---|---|---|---|
| `question` | string | 1–4000 chars | required |
| `top_k` | int | 1–20 | optional, default `RAG_TOP_K` (6) |
| `filters.ticker` | string | ≤6 chars | optional |
| `filters.source_type` | string | e.g. `sec_filing` | optional |
| `filters.date_start` / `date_end` | ISO date | start ≤ end | optional |

```bash
curl -s localhost:8000/query -H 'content-type: application/json' -d '{
  "question": "What was Apple'\''s total net sales in fiscal 2024?",
  "filters": {"ticker": "AAPL", "date_start": "2024-01-01"}
}'
```

**Response** (`models.schemas.QueryResponse`)

```json
{
  "answer": "Apple's fiscal 2024 total net sales were $391,035 million [1].",
  "citations": [
    {
      "source_id": "SEC:AAPL:10-K:2024-11-01",
      "title": "Apple Inc. 10-K filed 2024-11-01",
      "excerpt": "Total net sales were $391,035 million...",
      "url": "https://www.sec.gov/Archives/edgar/data/.../aapl-20240928.htm",
      "chunk_id": "9f83e2aa10b34d5c",
      "score": 0.9134,
      "section": "Item 7 - Management's Discussion and Analysis",
      "page_or_position": "chars 1200-2100"
    }
  ],
  "agent_path": ["classify", "rewrite", "embed", "retrieve", "generate"],
  "trace_url": null
}
```

A multi-hop question returns the same shape with an `agent_path` like
`["classify", "fetch", "extract", "compare", "synthesize"]`.

- **Citation guarantees (both paths):** every citation maps to an
  actually-retrieved chunk. Out-of-range or invented `[n]` markers are dropped;
  insufficient evidence produces an explicit refusal with no fabricated
  citations; agent-path degradation never removes citations, it rebuilds them
  from real chunks (see `docs/AGENT_DESIGN.md`). `trace_url` is a Langfuse link
  when tracing is configured, otherwise `null`.
- **Errors:** `503 no_embedding_provider`, `503 vector_store_not_ready`,
  `503 no_llm_provider`, `422 validation_error`. Internal node failures never
  surface as stack traces — they degrade into grounded partial answers with a
  `Limitations:` note in the answer text.

---

## POST /agents/query

Same contract as `/query` but **forces the multi-agent path**, bypassing the
classifier for the run while still recording `classify` in `agent_path`.
Use it when you know the question needs evidence gathering across sources —
or when the heuristic classifier would misroute an unusual phrasing.

```bash
curl -s localhost:8000/agents/query -H 'content-type: application/json' \
  -d '{"question": "Compare AAPL and MSFT revenue for FY2024"}'
```

**Response**: identical schema. Example `agent_path`:
`["classify", "fetch", "extract", "compare", "synthesize"]` (the `compare`
node is skipped automatically when extracted facts span a single entity and
period).

Guarantees specific to this route:

- Every fact carries server-forced `source_chunk_id` provenance — the model
  cannot invent where a number came from.
- Missing/conflicting comparison cells are flagged in the answer, never
  silently dropped.
- Unavailable sources (e.g. news without `NEWS_API_KEY`) are named in the
  answer's limitations instead of failing the request.
- Internal diagnostics (`node_errors`, ingestion keys) stay server-side; the
  response body is exactly `{answer, citations, agent_path, trace_url}`.

**Errors:** same envelope as `/query`.

---

## POST /ingest

Fetch → chunk → extract entities → embed → store, per document, idempotently.

**Request**

| Field | Type | Constraints | Notes |
|---|---|---|---|
| `source_type` | string | default `sec_filing` | must match a registered adapter |
| `ticker` | string | or `query` required | SEC ticker lookup |
| `query` | string | 2–500 chars | EDGAR full-text search path |
| `filing_type` | string | e.g. `10-K`, `8-K` | optional |
| `date_range` | `[start, end]` | ISO dates, start ≤ end | filing-date window |
| `limit` | int | 1–25, default 5 | max documents |

```bash
curl -s localhost:8000/ingest -H 'content-type: application/json' -d '{
  "ticker": "AAPL", "filing_type": "10-K",
  "date_range": ["2024-01-01", "2024-12-31"], "limit": 1
}'
```

**Response**

```json
{
  "documents_fetched": 1,
  "documents_ingested": 1,
  "chunks_indexed": 42,
  "chunks_truncated_for_metadata": 0,
  "documents_failed": 0,
  "failures": [],
  "embedding_provider": "openai",
  "embedding_model": "text-embedding-3-small",
  "duration_ms": 5127.3,
  "ok": true
}
```

Partial failure semantics: document-level errors do not abort the run — each
lands in `failures` as `{source_id, stage, error}` and remaining documents are
processed. A wholesale fetch failure returns `502 source_fetch_failed`.
Re-ingesting a source deletes its previous vectors first
(`DELETE_BEFORE_REINGEST=true`), so runs are safely repeatable.

---

## GET /sources

Which data sources are usable right now (`enabled && probe passed`):

```json
{ "sec_edgar": true, "news_api": false, "apex": false }
```

`news_api` reflects `NEWS_API_KEY` presence (Financial Modeling Prep by
default; `NEWS_API_PROVIDER` selects the registry entry). The optional APEX
adapter stays disabled unless explicitly enabled (`adapters.yaml`, spec
section 6.4).

## GET /providers

LLM provider chain state:

```json
{
  "available": ["openai", "ollama"],
  "generation_default": "openai",
  "embedding_available": true,
  "embedding_model": "text-embedding-3-small"
}
```

Availability probes are cached briefly; this endpoint forces a refresh.

## GET /health

Liveness. Always `200 {"status": "ok", "version": ..., "env": ...}` when the
process is up.

## GET /ready

Readiness = can actually serve `/query` and `/ingest`. Returns
`200 {"status": "ready", "checks": {...}}` when an embedding-capable provider
AND a configured vector store are present, otherwise
`503 {"status": ..., "checks": {...}}` under code `not_ready` with the failing
check named. The API intentionally starts without any credentials (degraded
mode) — endpoints respond consistently rather than crash-looping.

---

## Known limitations (Phase 3)

- No authentication, rate limiting, or CORS hardening — local-only.
- Routing heuristics are deterministic, not an LLM classifier; force the agent
  path with `/agents/query` when in doubt.
- News coverage depends on the configured provider's history window;
  earnings-call transcripts are not yet ingestible.
- `chunks_truncated_for_metadata > 0` means some chunk text was shortened to fit
  Pinecone's ~40KB metadata cap — retrieval fidelity for those chunks is reduced.
