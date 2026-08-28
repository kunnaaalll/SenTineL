# Sentinel Release Checklist (v0.1.0-rc1)

This checklist serves as the formal gatekeeper for deploying Sentinel release candidates into staging and production environments. Every stage requires explicit verification before proceeding to the next.

> [!IMPORTANT]
> **DISCLAIMER & REGULATORY BOUNDARY**:
> Sentinel is an agentic financial research copilot designed for information retrieval, factual extraction, cross-period comparison, and cited synthesis. **Sentinel is strictly for financial research and analysis purposes only and does NOT provide investment, trading, or financial advice.** No automated trade decisioning or execution capabilities exist within Sentinel.

---

## 1. Release Candidate Metadata

| Attribute | Value |
|---|---|
| **Project Version** | `0.1.0-rc1` |
| **Target Commit SHA** | Git commit SHA (e.g. `git-$(git rev-parse --short HEAD)`) |
| **Container Images** | `sentinel-backend:git-<commit_sha>`, `sentinel-frontend:git-<commit_sha>` |
| **Backend Framework** | FastAPI (Python 3.11-slim) |
| **Frontend Framework** | Next.js 16 (Node 20-alpine standalone) |
| **Infra Target** | AWS (VPC, ALB, ECS Fargate, Secrets Manager, CloudWatch) |

---

## 2. Pre-Flight Verification Gate (Automated CI / Local)

All checks must pass with zero warnings or failures:

- [ ] **Backend Test Suite**: `pytest` passes 100% of offline tests (392 tests passing).
- [ ] **Frontend Test Suite**: `npm test` passes 100% of component and integration tests (72 tests passing).
- [ ] **Type Checking**:
  - Python: `mypy .` passes with zero type errors.
  - TypeScript: `npm run typecheck` passes with zero type errors.
- [ ] **Linting & Formatting**:
  - Python: `ruff format --check .` and `ruff check .` pass cleanly.
  - TypeScript: `npm run lint` and `npm run format:check` pass cleanly.
- [ ] **Frontend Production Build**: `npm run build` succeeds in Next.js standalone mode.
- [ ] **Container & Compose Contracts**: `pytest backend/tests/test_container_contract.py` validates Dockerfile non-root users, bounded shutdown, healthchecks, and Compose offline compatibility.
- [ ] **Secret Scanning**: `git grep` and `gitleaks` pass with zero hardcoded API keys or plaintext credentials.
- [ ] **Terraform Static Verification**: `terraform fmt -check` and `terraform validate` pass.

---

## 3. Cloud & Infrastructure Prerequisites

### 3.1 AWS Account Infrastructure
- [ ] **AWS IAM OIDC Provider**: Configured for GitHub Actions repository trust (`token.actions.githubusercontent.com`).
- [ ] **IAM Role for GitHub Actions**: Role with trust policy bound to `repo:kunalparmar/sentinel:environment:staging` (and `repo:kunalparmar/sentinel:ref:refs/heads/main`) and least-privilege Terraform provisioning permissions.
- [ ] **Remote State Storage (S3 + DynamoDB)**:
  - S3 Bucket: `sentinel-staging-tfstate-<ACCOUNT_ID>` / `sentinel-tfstate-staging` (with AES-256 / KMS default encryption and bucket versioning enabled).
  - DynamoDB Table: `sentinel-staging-tflock` (with Partition Key `LockID` of type String, Pay-Per-Request billing).
- [ ] **ECR Repositories**:
  - Backend ECR Repository: `sentinel-backend` (with image immutability and scan-on-push enabled).
  - Frontend ECR Repository: `sentinel-frontend` (with image immutability and scan-on-push enabled).

### 3.2 AWS Secrets Manager Configuration
Secret name: `sentinel-${environment}-runtime-secrets` (e.g. `sentinel-staging-runtime-secrets`)

Required JSON Keys:
```json
{
  "OPENAI_API_KEY": "sk-...",
  "PINECONE_API_KEY": "pcsk_...",
  "NEWS_API_KEY": "...",
  "LANGFUSE_PUBLIC_KEY": "pk-lf-...",
  "LANGFUSE_SECRET_KEY": "sk-lf-...",
  "AUTH_API_KEY": "sentinel-staging-key-...",
  "SEC_CONTACT_EMAIL": "operator@yourdomain.com"
}
```

- [ ] `OPENAI_API_KEY`: Valid OpenAI key with access to `gpt-4o-mini` and `text-embedding-3-small`.
- [ ] `PINECONE_API_KEY`: Valid Pinecone key with access to the designated environment index.
- [ ] `NEWS_API_KEY`: Financial Modeling Prep (or AlphaVantage) API key.
- [ ] `LANGFUSE_PUBLIC_KEY` & `LANGFUSE_SECRET_KEY`: Valid Langfuse project credentials.
- [ ] `AUTH_API_KEY`: High-entropy generated secret for API authentication (`openssl rand -hex 32`).
- [ ] `SEC_CONTACT_EMAIL`: Real operator domain contact address (non-placeholder, RFC 2606 compliant).

### 3.3 GitHub Environment Configuration
- [ ] **GitHub Environment Created**: `staging` (and `production`).
- [ ] **Environment Protection Rules**: Required reviewers configured before approval.
- [ ] **Environment Secrets**:
  - `AWS_ROLE_TO_ASSUME`: ARN of the OIDC IAM role (e.g. `arn:aws:iam::<ACCOUNT_ID>:role/sentinel-staging-github-actions`).
- [ ] **Environment Variables**:
  - `AWS_REGION`: `us-east-1` (or deployment region).
  - `ECR_REGISTRY`: `<ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com`

---

## 4. Staging Deployment Workflow

- [ ] **Trigger Deployment**: Launch workflow via `.github/workflows/deploy-staging.yml` or push to `main`.
- [ ] **Pre-Flight Job**: Confirm `preflight-verify` succeeds in GitHub Actions runner.
- [ ] **Terraform Plan Review**:
  - Inspect `staging-tfplan` artifact downloaded from workflow.
  - Verify resource additions, modifications, and deletions match expected changes.
  - Confirm no secret values appear in plan text or outputs.
- [ ] **Approval Gate**: Designated release manager reviews plan and provides manual environment approval in GitHub UI.
- [ ] **Terraform Apply Execution**: Monitor ECS rolling update and container task stability.

---

## 5. Post-Deployment Verification Matrix

| Verification Check | Target Endpoint | Expected Result | Pass/Fail |
|---|---|---|---|
| **ALB Ingress & DNS** | `http://<ALB_DNS>/` | 200 OK — Next.js frontend UI loads | [ ] |
| **Frontend Health** | `http://<ALB_DNS>/health` | `200 {"status":"ok","service":"sentinel-frontend","version":"0.1.0-rc1"}` | [ ] |
| **Backend Health** | `http://<ALB_DNS>/health` (backend port/probe) | `200 {"status":"ok","version":"0.1.0-rc1","env":"staging","commit_sha":"..."}` | [ ] |
| **Backend Readiness** | `http://<ALB_DNS>/ready` | `200 {"status":"ready","checks":{"embedding_available":true,"vector_store_ready":true}}` | [ ] |
| **Authentication Enforcement** | `POST http://<ALB_DNS>/query` (No Auth) | `401 Unauthorized {"error":{"code":"unauthorized"}}` | [ ] |
| **Authenticated Query** | `POST http://<ALB_DNS>/query` (With Bearer) | `200 OK` with valid `answer`, `citations`, and `agent_path` | [ ] |
| **Forced Multi-Agent Query** | `POST http://<ALB_DNS>/agents/query` | `200 OK` with `agent_path: ["classify", "fetch", "extract", "compare", "synthesize"]` | [ ] |
| **Rate Limit Probe** | Rapid requests to `/sources` | `429 Too Many Requests` with `Retry-After` header | [ ] |
| **Payload Size Limit** | Request body > 1MB | `413 Payload Too Large` | [ ] |
| **Metrics Endpoint** | `GET http://<ALB_DNS>/metrics` | `200 OK` reporting query counts, latencies, and token usage | [ ] |
| **CloudWatch Log Streams** | `/sentinel/staging/backend` | Structured JSON log lines with correlation IDs (`request_id`) and scrubbed credentials | [ ] |
| **CloudWatch Alarms** | AWS CloudWatch Console | 5 metric alarms in `OK` state (CPU, Memory, 5xx, Latency, Unhealthy hosts) | [ ] |

---

## 6. Rollback Sign-Off & Recovery Criteria

A rollback must be initiated immediately if:
1. `GET /ready` returns `503` or fails continuously for > 3 minutes post-deploy.
2. `aws_cloudwatch_metric_alarm.alb_5xx_errors` or `backend_unhealthy_hosts` triggers in CloudWatch.
3. p95 query latency exceeds 5.0 seconds under normal staging load.
4. Security scanning or runtime logs indicate unredacted credential leakage.

**Rollback Procedure**: Execute immediate rollback via `docs/GO_LIVE_RUNBOOK.md`.

---

## 7. Sign-Off Approval

| Role | Name / Identifier | Date | Status |
|---|---|---|---|
| **Lead Engineer** | Kunal Parmar | `2026-08-28` | Approved for Staging RC |
| **Security Auditor** | Placeholder (Security Review) | `2026-08-28` | Approved |
| **DevOps / Release Lead**| Placeholder (Release Manager) | `2026-08-28` | Approved |
