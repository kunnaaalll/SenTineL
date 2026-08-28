# Sentinel Security Architecture & Hardening Guide

This document outlines the security controls, threat model, authentication mechanisms, network segmentation, secrets lifecycle, and container hardening policies implemented in Sentinel.

---

## 1. Threat Model & Security Posture

Sentinel is an agentic financial research copilot intended for deployment in staging and production environments. The threat model accounts for:
- **Unauthorized API Access**: Single-user deployment model protected by constant-time token verification.
- **Denial of Service / Resource Exhaustion**: Token-bucket rate limiting and strict maximum request body constraints (413).
- **Prompt Injection & Data Tampering**: Strict system prompt constraints, citation verification against real retrieved chunk hashes, and validation of agent inputs/outputs.
- **Credential & Data Leakage**: Redaction of provider keys (`sk-*`, `pcsk_*`, `pk-lf-*`, `Bearer *`) from all structured logs, non-leakage error envelopes, and `SecretStr` memory obfuscation.
- **Cross-Site Attacks**: Strict CORS origin whitelisting, Host header verification, and defense-in-depth HTTP security headers (`nosniff`, `DENY`, `strict-origin-when-cross-origin`, CSP).

---

## 2. API Protection & Authentication

### Single-User Staging Authentication
In staging and production, authentication is enforced on all API endpoints via `AuthenticationMiddleware` (`backend/api/middleware.py`).

- **Supported Headers**:
  - `Authorization: Bearer <SENTINEL_AUTH_API_KEY>`
  - `X-API-Key: <SENTINEL_AUTH_API_KEY>`
- **Constant-Time Comparison**:
  Comparisons use `hmac.compare_digest` to prevent timing side-channel attacks against the secret key.
- **Exempt Endpoints**:
  - `GET /health` (Liveness probe for load balancers)
  - `GET /ready` (Readiness check for cluster schedulers)
  - `GET /docs`, `GET /redoc`, `GET /openapi.json` (OpenAPI documentation)

### Rate Limiting
- **Algorithm**: Thread-safe in-memory token bucket.
- **Configuration**:
  - `SENTINEL_RATE_LIMIT_REQUESTS_PER_MINUTE`: Baseline sustained request rate (default: 60 rpm).
  - `SENTINEL_RATE_LIMIT_BURST_LIMIT`: Short-term burst allowance (default: 10).
- **429 Response**:
  Returns standard machine error envelope with `Retry-After: <seconds>` HTTP response header.

### Request Size Limits
- **Maximum Payload**: Configurable via `SENTINEL_MAX_REQUEST_BODY_BYTES` (default: 1,048,576 bytes / 1MB).
- **413 Response**:
  Rejects oversized request bodies before memory allocation with `payload_too_large` error code.

### Security Headers
`SecurityHeadersMiddleware` injects standard protective headers into every HTTP response:
```http
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 1; mode=block
Referrer-Policy: strict-origin-when-cross-origin
Content-Security-Policy: default-src 'self'; img-src 'self' data: https:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'
```

---

## 3. Network Architecture & Segmentation

The staging infrastructure (`infra/terraform/`) enforces strict network boundary isolation:

```
[ Internet ]
     |
     v (Port 80/443 - Public CIDRs)
[ Internet-Facing ALB (Public Subnets) ]
     |
     +-------------------------------+ (Port 3000 only)
     |                               v
     |                [ Frontend ECS Fargate ]
     |                (Private Subnets: No Ingress from Internet)
     |                               |
     +-------------------------------+ (Port 8000 only)
                                     v
                          [ Backend ECS Fargate ]
                          (Private Subnets: No Ingress from Internet)
                                     |
                                     v (Port 443 VPC Endpoints & NAT)
                          [ AWS Services & External APIs ]
                          - AWS Secrets Manager
                          - CloudWatch Logs
                          - ECR (api & dkr)
                          - S3 Gateway
                          - Pinecone / OpenAI / SEC EDGAR via NAT GW
```

### Security Group Rules
- **ALB Security Group**: Allows inbound 80/443 from allowed CIDRs (`0.0.0.0/0` or corporate VPN IP ranges). Outbound only to Frontend (port 3000) and Backend (port 8000) security groups.
- **Frontend Security Group**: Allows inbound port 3000 **strictly** from the ALB security group. Ingress from `0.0.0.0/0` is blocked.
- **Backend Security Group**: Allows inbound port 8000 **strictly** from the ALB and Frontend security groups.
- **VPC Endpoints Security Group**: Allows inbound port 443 strictly from ECS compute instances within the private subnets.

---

## 4. Secrets Management & Lifecycle

1. **Zero Secret Persistence**:
   - Secrets are never written to disk, Git repositories, or baked into Docker container layers.
   - Local `.env` files are strictly excluded via `.gitignore`.
2. **AWS Secrets Manager**:
   - Production and staging secrets reside in AWS Secrets Manager (`/sentinel/${env}/runtime-secrets`).
   - ECS Task Definitions pull secret values dynamically at container launch time using least-privilege IAM policies (`secretsmanager:GetSecretValue`).
3. **In-Memory Protection**:
   - Settings in `backend/config/settings.py` wrap credentials (`openai_api_key`, `anthropic_api_key`, `pinecone_api_key`, `news_api_key`, `langfuse_secret_key`, `auth_api_key`) in Pydantic `SecretStr` to prevent accidental serialization in `repr()`, stack traces, or exception dumps.
4. **Log Sanitization**:
   - `backend/observability/logging.py` employs regular-expression scrubbing that catches and masks tokens matching `sk-*`, `pcsk_*`, `pk-lf-*`, `Bearer *`, and key patterns before emitting JSON log lines.

---

## 5. Container & Runtime Hardening

- **Non-Root Execution**:
  Backend container runs under unprivileged service user `sentinel` (UID 10001); Frontend runs under `node` (UID 1000).
- **Minimal Base Images**:
  Backend uses pinned `python:3.11-slim-trixie` with build dependencies purged in a multi-stage build; Frontend uses multi-stage `node:20-alpine` with standalone output.
- **Read-Only Root Filesystems**:
  Container designs keep filesystems immutable at runtime (`read_only: true`); state is strictly ephemeral or managed externally (tmpfs on `/tmp`).
- **Readiness vs Liveness Probes**:
  ALB performs continuous `/health` checks to eliminate broken tasks immediately without risking traffic drops.

---

## 6. Release Governance & Operational Runbooks

- **Release Checklist**: [RELEASE_CHECKLIST.md](file:///Applications/My%20Mac/Development/Projects/SenTineL/docs/RELEASE_CHECKLIST.md)
- **Go-Live Runbook**: [GO_LIVE_RUNBOOK.md](file:///Applications/My%20Mac/Development/Projects/SenTineL/docs/GO_LIVE_RUNBOOK.md)

> [!IMPORTANT]
> **Research-Only Scope**:
> Sentinel is an informational research and analysis copilot. It does not execute trades, manage funds, or provide personalized financial recommendations.
