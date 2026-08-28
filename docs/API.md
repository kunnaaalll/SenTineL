# Sentinel API Specification

FastAPI service exposing document ingestion, complexity-routed query paths (simple RAG vs multi-agent), provider status, and operational metrics.

---

## 1. Authentication & Security

In staging and production environments, Sentinel enforces single-user API protection (`backend/api/middleware.py`).

### Authentication Headers
All non-exempt endpoints accept either:
- `Authorization: Bearer <SENTINEL_AUTH_API_KEY>`
- `X-API-Key: <SENTINEL_AUTH_API_KEY>`

```bash
curl -H "Authorization: Bearer your-staging-api-key" https://api.sentinel.internal/query ...
```

**Exempt Routes** (No auth required):
- `GET /health` (Load balancer liveness probe)
- `GET /ready` (Scheduler readiness probe)
- `GET /docs`, `GET /redoc`, `GET /openapi.json` (OpenAPI documentation)

### Request Correlation (`X-Request-ID`)
Clients can pass a custom `X-Request-ID` header. If omitted, Sentinel generates a unique UUID4 and returns it on every HTTP response for distributed log correlation.

### Rate Limiting & Protection
- **Rate Limit**: Default 60 requests/minute with a burst allowance of 10 (`429 rate_limited` with `Retry-After: <seconds>` header).
- **Body Size Limit**: Default 1MB (`413 payload_too_large`).
- **Security Headers**: Standard defense-in-depth headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `X-XSS-Protection`, `Referrer-Policy`, `Content-Security-Policy`).

---

## 2. Standard Error Envelope

All error responses across all HTTP status codes return a uniform JSON envelope:

```json
{
  "error": {
    "code": "machine_readable_code",
    "message": "Human readable summary of the error",
    "details": []
  }
}
```

### Common Error Codes

| Status | Code | Meaning |
|---|---|---|
| 400 | `bad_request` | Malformed request structure |
| 401 | `unauthorized` | Missing or invalid API key / Bearer token |
| 403 | `forbidden` | Action not permitted |
| 404 | `not_found` | Resource or route not found |
| 405 | `method_not_allowed` | HTTP method not supported for route |
| 413 | `payload_too_large` | Request body exceeds configured byte limit |
| 422 | `validation_error` | Request parameters failed schema validation |
| 429 | `rate_limited` | Request quota exceeded (check `Retry-After`) |
| 500 | `internal_server_error` | Unhandled internal exception (trace in logs) |
| 502 | `source_fetch_failed` | External data source (EDGAR, News) unreachable |
| 503 | `not_ready` / `vector_store_not_ready` / `no_embedding_provider` | Dependencies unavailable |
| 504 | `gateway_timeout` | Upstream provider timed out |

---

## 3. Endpoints

### POST /query
Single question-answering entry point with **automatic complexity routing**.
- **simple**: One entity, single period → Single-pass RAG (`rewrite → embed → retrieve → generate`).
- **multi_hop**: Multiple entities or comparison words → Multi-agent team (`fetch → extract → compare → synthesize`).

**Request**
```json
{
  "question": "What was Apple's total net sales in fiscal 2024?",
  "top_k": 5,
  "filters": {
    "ticker": "AAPL",
    "date_start": "2024-01-01"
  }
}
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

---

### POST /agents/query
Forces the multi-agent research path (`fetch → extract → compare → synthesize`) regardless of heuristic classification.

**Request**
```json
{
  "question": "Compare AAPL and MSFT revenue and gross margins for FY2024"
}
```

**Response**: Identical `QueryResponse` envelope with multi-agent execution path.

---

### POST /ingest
Fetches documents from SEC EDGAR or news providers, chunks prose and tables, extracts financial entities, generates vector embeddings, and stores vectors into Pinecone.

**Request**
```json
{
  "source_type": "sec_filing",
  "ticker": "AAPL",
  "filing_type": "10-K",
  "date_range": ["2024-01-01", "2024-12-31"],
  "limit": 1
}
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

---

### GET /metrics
Operational metrics snapshot for monitoring and alerting.

**Response**
```json
{
  "uptime_seconds": 1240.5,
  "http": {
    "total_requests": 340,
    "status_codes": {
      "200": 335,
      "401": 2,
      "429": 3
    },
    "rate_limit_rejections": 3,
    "auth_rejections": 2,
    "payload_too_large_rejections": 0,
    "routes": {
      "POST /query": { "requests": 210, "errors": 0, "avg_duration_ms": 350.2 }
    }
  },
  "queries": {
    "total": 210,
    "simple": 180,
    "multi_hop": 30,
    "citations_returned": 580,
    "avg_duration_ms": 350.2
  },
  "ingestion": {
    "documents_ingested": 12,
    "chunks_indexed": 410,
    "documents_failed": 0
  },
  "providers": {
    "calls": { "openai": 240 },
    "errors": { "openai": 0 },
    "tokens": {
      "openai": { "prompt_tokens": 82000, "completion_tokens": 14000, "total_tokens": 96000 }
    }
  }
}
```

---

### GET /health
Liveness probe. Returns `200 {"status": "ok", "version": "0.1.0-rc1", "env": "staging", "commit_sha": "git-049a0db"}` while the process is running.

---

### GET /ready
Readiness probe. Returns `200 {"status": "ready", "checks": {...}}` when an embedding provider and vector store are configured and healthy, or `503` with degraded details when unconfigured.

---

### GET /sources
Returns status and availability of configured data adapters (`sec_edgar`, `news_api`, `apex`).

---

### GET /providers
Returns list of available LLM providers, active embedding model, and default generation provider.
