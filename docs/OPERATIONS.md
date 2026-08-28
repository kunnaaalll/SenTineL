# Sentinel Operations & Observability Guide

This document describes standard operating procedures, observability practices, log querying, metrics collection, alerting thresholds, and incident recovery runbooks for Sentinel in staging and production environments.

---

## 1. Observability Architecture

Sentinel is instrumented across three core observability pillars:
1. **Structured JSON Logs**: Machine-readable single-line JSON logs emitted to `stdout`, ingested by AWS CloudWatch Logs, and correlated by `request_id`.
2. **Operational Metrics**: In-process thread-safe metric collector exposed at `GET /metrics`, reporting HTTP request counts, status codes, query latencies, vector retrieval stats, and LLM token usage.
3. **Distributed Tracing & Evaluation**: Optional Langfuse trace correlation tracking RAG spans (`classify`, `rewrite`, `embed`, `retrieve`, `generate`, `synthesize`).

```
[ Client / WebUI ]
       |
       | (X-Request-ID: req-uuid4)
       v
[ Application Load Balancer ]
       |
       +---------------------------------------------+
       |                                             |
       v                                             v
[ Frontend ECS Task ]                         [ Backend ECS Task ]
  - Next.js stdout                              - RequestIdFilter & ContextVar
  - Client errors                               - JsonLogFormatter
                                                - MetricsRegistry (/metrics)
                                                - Langfuse Tracer (spans & scores)
       |                                             |
       +----------------------+----------------------+
                              |
                              v
                  [ AWS CloudWatch Logs ]
                   /sentinel/staging/backend
                   /sentinel/staging/frontend
```

---

## 2. Structured JSON Logging

When `SENTINEL_LOG_FORMAT=json` (default in staging and production), the backend outputs structured JSON lines:

### Log Schema

| Field | Type | Description |
|---|---|---|
| `timestamp` | string (ISO 8601 UTC) | Precise UTC event timestamp |
| `level` | string | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `logger` | string | Originating Python logger/module |
| `message` | string | Sanitized event message (`sk-*`, `Bearer *`, etc. scrubbed) |
| `request_id` | string | Correlation ID propagated from `X-Request-ID` or generated UUID4 |
| `env` | string | Deployment environment (`dev`, `staging`, `prod`) |
| `error` | object (optional) | Exception `type` and `message` when an error occurs |
| `stack_trace` | string (optional) | Multi-line traceback formatted for debugging |

### Example Log Output

```json
{
  "timestamp": "2026-08-28T14:02:15.123456+00:00",
  "level": "INFO",
  "logger": "api.routes_query",
  "message": "Query executed: AAPL revenue FY2024 (citations: 2, duration: 420.5ms)",
  "request_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "env": "staging"
}
```

---

## 3. CloudWatch Logs Insights Queries

Use these pre-built CloudWatch Insights queries to analyze system health and debug incidents:

### 1. Request Latency Distribution (p50, p90, p99)
```sql
fields @timestamp, request_id, @message
| filter @message like /duration/
| parse @message "duration: *ms" as duration_ms
| stats avg(duration_ms) as avg_ms, pct(duration_ms, 50) as p50_ms, pct(duration_ms, 90) as p90_ms, pct(duration_ms, 99) as p99_ms by bin(5m)
```

### 2. Error and Exception Rate
```sql
fields @timestamp, logger, error.type, error.message, request_id
| filter level = "ERROR" or ispresent(error)
| stats count(*) as error_count by error.type, bin(5m)
| sort error_count desc
```

### 3. Trace a Single Request End-to-End
```sql
fields @timestamp, level, logger, message, error.message
| filter request_id = "YOUR-REQUEST-ID-HERE"
| sort @timestamp asc
```

### 4. Rate Limiting & Auth Rejection Volume
```sql
fields @timestamp, request_id, logger, message
| filter message like /401/ or message like /429/
| stats count(*) as rejections by bin(1m)
```

---

## 4. Operational Metrics (`GET /metrics`)

The endpoint `GET /metrics` provides an operational snapshot:

```json
{
  "uptime_seconds": 18420.5,
  "http": {
    "total_requests": 1420,
    "status_codes": {
      "200": 1390,
      "401": 5,
      "429": 10,
      "500": 15
    },
    "rate_limit_rejections": 10,
    "auth_rejections": 5,
    "payload_too_large_rejections": 0,
    "routes": {
      "POST /query": { "requests": 850, "errors": 10, "avg_duration_ms": 380.2 },
      "POST /ingest": { "requests": 40, "errors": 0, "avg_duration_ms": 1150.0 }
    }
  },
  "queries": {
    "total": 850,
    "simple": 700,
    "multi_hop": 150,
    "citations_returned": 2450,
    "avg_duration_ms": 380.2
  },
  "ingestion": {
    "documents_ingested": 40,
    "chunks_indexed": 820,
    "documents_failed": 0
  },
  "providers": {
    "calls": { "openai": 950, "anthropic": 120 },
    "errors": { "openai": 2, "anthropic": 0 },
    "tokens": {
      "openai": { "prompt_tokens": 450200, "completion_tokens": 98400, "total_tokens": 548600 }
    }
  }
}
```

---

## 5. CloudWatch Alarms & Monitoring Matrix

| Metric / Alarm | Threshold | Evaluation Period | Action |
|---|---|---|---|
| **ALB 5XX Errors** | > 5 errors | 1 minute (2 consecutive datapoints) | SNS Alert -> PagerDuty / Slack |
| **ALB Target Response Time** | > 2.5 seconds | 5 minutes (2 consecutive datapoints) | SNS Alert |
| **ECS CPU Utilization** | > 80% | 5 minutes | Scale out task count & notify |
| **ECS Memory Utilization** | > 85% | 5 minutes | Investigate memory leak / scale |
| **Unhealthy Host Count** | >= 1 host | 1 minute (2 consecutive datapoints) | Immediate alert |

---

## 6. Incident Recovery Runbooks

### Runbook 1: Primary LLM Provider Outage (OpenAI/Anthropic)
- **Symptom**: `GET /ready` returns 503 or queries fail with `503 Service Unavailable` (`no_embedding_provider`).
- **Diagnosis**:
  1. Check CloudWatch Insights for `logger = "llm_providers.engine"` or `error.type = "ProviderUnavailableError"`.
  2. Inspect provider status dashboards (e.g. OpenAI Status, Anthropic Status).
- **Remediation**:
  1. Update AWS Secrets Manager secret `/sentinel/staging/runtime-secrets` to configure alternative provider API keys if fallback is available.
  2. Restart ECS backend task:
     ```bash
     aws ecs update-service --cluster sentinel-staging-cluster --service sentinel-staging-backend --force-new-deployment
     ```

### Runbook 2: Rate Limit Spike / Denial of Service
- **Symptom**: High volume of `429 Too Many Requests` or elevated CPU.
- **Diagnosis**:
  1. Query CloudWatch Insights for IP addresses or client IDs triggering rate limits.
  2. Check `METRICS.get_snapshot()["http"]["rate_limit_rejections"]`.
- **Remediation**:
  1. If traffic is malicious, block offending CIDRs via ALB Security Group or WAF rule.
  2. If legitimate load requires higher thresholds, adjust `SENTINEL_RATE_LIMIT_REQUESTS_PER_MINUTE` and `SENTINEL_RATE_LIMIT_BURST_LIMIT` in task environment or Secrets Manager and redeploy.

### Runbook 3: Pinecone / Vector Store Unavailability
- **Symptom**: `GET /ready` returns `vector_store_ready: false` and queries fail with `vector_store_not_ready`.
- **Diagnosis**:
  1. Verify `PINECONE_API_KEY` and `PINECONE_INDEX_NAME`.
  2. Test index connectivity from private subnet via VPC endpoint / NAT.
- **Remediation**:
  1. If Pinecone index is degraded, verify AWS PrivateLink / NAT gateway routing in `networking.tf`.
  2. If credentials expired, rotate in Secrets Manager and force new ECS deployment.

---

## 7. Backup and Disaster Recovery

1. **State Independence**: Sentinel backend is stateless; all persistent chunk vectors and metadata reside in Pinecone.
2. **Infrastructure Recovery**: Full VPC, ECS cluster, ALB, and CloudWatch configurations are codified in Terraform (`infra/terraform/`). In a disaster recovery scenario:
   ```bash
   cd infra/terraform
   terraform init
   terraform apply -var-file=envs/staging.tfvars
   ```
3. **Index Rehydration**: To rebuild the vector store from scratch, re-run ingestion pipelines via `POST /ingest` for target tickers and filing periods.

---

## 8. Release Operations & Checklists

- **Release Checklist**: [RELEASE_CHECKLIST.md](file:///Applications/My%20Mac/Development/Projects/SenTineL/docs/RELEASE_CHECKLIST.md)
- **Go-Live Runbook**: [GO_LIVE_RUNBOOK.md](file:///Applications/My%20Mac/Development/Projects/SenTineL/docs/GO_LIVE_RUNBOOK.md)

> [!IMPORTANT]
> **Operational Disclaimer**:
> Sentinel is an automated financial research tool. It does not provide trading or investment advice. All operational runbooks are designed for platform reliability and security governance.
