# Changelog

All notable changes to Sentinel are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0-rc1] - 2026-08-28

### Added
- **Agentic Financial Research Copilot**: Autonomous query classification, multi-hop financial query decomposition, LangGraph agent team (`fetch` -> `extract` -> `compare` -> `synthesize`), and single-pass RAG chain.
- **Data Ingestion Pipeline**: Financial document chunker (table-atomic, footnote-attaching, prose-splitting), regex + LLM entity extraction (tickers, fiscal periods, monetary metrics), and Pinecone vector store indexing.
- **Data Source Adapters**: Live SEC EDGAR full-text search and filing parser, Financial Modeling Prep news adapter, and mockable adapter interface.
- **FastAPI Backend**: Routed endpoints (`/query`, `/agents/query`, `/ingest`, `/sources`, `/providers`, `/health`, `/ready`, `/metrics`).
- **Security & API Protection**:
  - Single-user staging authentication via `Authorization: Bearer` and `X-API-Key` headers with constant-time comparison (`secrets.compare_digest`).
  - Thread-safe token-bucket rate limiter with `429` status code and `Retry-After` headers.
  - Maximum request body size enforcement (`413` status code).
  - Defensive security headers (`nosniff`, `DENY`, `strict-origin-when-cross-origin`, CSP).
  - CORS origin and Trusted Host filtering.
  - `SecretStr` credential wrapping and structured JSON log sanitization.
- **Observability & Operations**:
  - Structured single-line JSON logging with request correlation IDs (`X-Request-ID`).
  - Runtime operational metrics collector (`GET /metrics`) tracking latency, HTTP status codes, query types, and token usage.
  - Optional Langfuse distributed tracing.
- **Next.js 16 Web Interface**:
  - Reactive chat interface with message bubbles, Markdown formatting, citation cards with source URLs, and agent trace viewer.
  - Document browsing and filing ingestion panel (`/sources`).
  - Backend proxying via `BACKEND_ORIGIN` Next.js server rewrite (zero browser credentials).
- **Production-Grade Terraform Infrastructure**:
  - Dedicated multi-AZ VPC, public/private subnets, Internet Gateway, NAT Gateway, and VPC Endpoints for S3, ECR, Secrets Manager, and CloudWatch Logs.
  - Least-privilege Security Groups and IAM ECS Task Execution / Task roles.
  - ECS Fargate cluster with Container Insights, Application Load Balancer with path-based routing.
  - CloudWatch metric alarms for CPU, Memory, 5xx errors, Target Latency, and Unhealthy hosts with SNS alerts.
- **CI/CD & Release Controls**:
  - GitHub Actions CI workflow verifying backend tests (392 tests), frontend checks (72 tests), container contracts, Docker Compose, Terraform formatting/init/validation, and secret scanning.
  - Approval-gated staging deployment workflow (`deploy-staging.yml`) with pre-flight verification, OIDC short-lived credentials, and immutable commit SHA tags.
  - Release checklist (`docs/RELEASE_CHECKLIST.md`) and Go-Live runbook (`docs/GO_LIVE_RUNBOOK.md`).

### Fixed
- Fixed ALB listener rule syntax reference for optional SSL targets in `infra/terraform/alb.tf`.
- Updated ALB backend routing rules to include OpenAPI documentation endpoints (`/docs`, `/redoc`, `/openapi.json`) and removed direct `/backend/*` interception.
- Protected production secrets in Secrets Manager with a 30-day recovery window.
- Included internal VPC CIDR in ALB security group ingress for private container communication.
- Added `commit_sha` to health status responses and unified project versioning across backend, frontend, and API documentation.

### Explicit Non-Goals (v1)
- APEX adapter remains disabled by default.
- No automated trading, portfolio execution, or financial advisory decisioning (research and analysis only).
- No multi-user auth / session management.
