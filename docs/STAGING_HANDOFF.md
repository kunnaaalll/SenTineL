# Sentinel Staging Deployment Handoff (`v0.1.0-rc1`)

**Document Version**: 1.0.0  
**Target Version**: `v0.1.0-rc1`  
**Deployment Target**: AWS Staging Environment (VPC, ALB, ECS Fargate, Secrets Manager, CloudWatch)  
**Status**: Ready for Staging Plan Review (Approval-Gated)  
**Release Lead**: Kunal Parmar  

> [!IMPORTANT]
> **DISCLAIMER & REGULATORY BOUNDARY**:  
> Sentinel is an agentic financial research copilot designed for information retrieval, structured factual extraction, cross-period financial comparison, and cited synthesis. **Sentinel is strictly for research and analysis purposes only and does NOT provide investment, financial, or trading advice.** Sentinel executes zero trades and does not manage investment portfolios.

---

## 1. Preflight Audit Summary & Verification Matrix

A comprehensive preflight audit across all Terraform configurations, CI/CD workflows, container definitions, application settings, and security controls was performed.

| Audit Area | Item Checked | Status | Notes |
|---|---|---|---|
| **Account & Region** | No hardcoded account IDs or regions in code | **PASSED** | Region defaults to `us-east-1` via variable; accounts dynamic via data sources |
| **State Configuration** | S3 + DynamoDB remote state documented & isolated | **PASSED** | Key path `sentinel/staging/terraform.tfstate`, server-side encryption + locking |
| **Staging Variables** | `staging.tfvars.example` matches `variables.tf` | **PASSED** | Strict validations on CIDRs, ports, Fargate sizing, log retention |
| **Container References** | ECS task definitions reference immutable image tags | **PASSED** | Default `git-<sha>`; registry prefix configurable via `vars.ECR_REGISTRY` |
| **Health Check Routes** | Ingress & container health paths match implementation | **PASSED** | Frontend: `/health` (port 3000); Backend: `/health` & `/ready` (port 8000) |
| **ALB Routing** | Path routing for API, OpenAPI, metrics, UI | **PASSED** | Priority 10 routes `/query`, `/agents/*`, `/ingest`, `/sources`, `/providers`, `/ready`, `/metrics`, `/docs`, `/redoc`, `/openapi.json` to Backend; default to Frontend |
| **Security Groups** | Least-privilege network segmentation | **PASSED** | ALB: 80/443 ingress; Frontend: port 3000 from ALB only; Backend: port 8000 from ALB & Frontend only; VPCE: port 443 from ECS only |
| **IAM Permissions** | Least-privilege execution & task roles | **PASSED** | Scoped `secretsmanager:GetSecretValue` on runtime secret ARN only |
| **Secrets Resolution** | Secrets Manager keys match application configuration | **PASSED** | Injected: `OPENAI_API_KEY`, `PINECONE_API_KEY`, `NEWS_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `AUTH_API_KEY`, `SEC_CONTACT_EMAIL` |
| **Security Controls** | Rate limiting, CORS, Trusted Hosts, Body Limits, Headers | **PASSED** | Token bucket (120 rpm, burst 30), 1MB payload cap, constant-time auth comparison |
| **Secret Sanitization** | Logs, build contexts, and artifacts scrub credentials | **PASSED** | `.dockerignore` / `.gitignore` exclude secrets; regex log scrubber masks sensitive tokens |

### Confirmed Fix Applied During Preflight
- **ECS Task Definition Secrets (`infra/terraform/ecs.tf`)**: Injected `LANGFUSE_PUBLIC_KEY` alongside `LANGFUSE_SECRET_KEY` from Secrets Manager into the backend container environment so that Langfuse tracing initializes properly when credentials are provided.
- **CI/CD Smoke Verification (`.github/workflows/deploy-staging.yml`)**: Added an automated post-deployment health verification step checking ALB `/health` and `/ready` to prevent deployment promotion on failure.

---

## 2. Required Deployment Configuration & Cloud Prerequisites

The following environment variables and secrets must be configured in AWS Secrets Manager and GitHub Actions prior to running `terraform apply`. **Values must never be committed to Git or printed in plain text.**

### 2.1 AWS Secrets Manager (`/sentinel/staging/runtime-secrets` or `${project_name}-${environment}-runtime-secrets`)

| Key Name | Type | Description / Format | Gating Requirement |
|---|---|---|---|
| `OPENAI_API_KEY` | Secret (`SecretStr`) | OpenAI API key (`sk-proj-...` or `sk-...`) | Mandatory for LLM generation & embeddings |
| `PINECONE_API_KEY` | Secret (`SecretStr`) | Pinecone API key (`pcsk_...`) | Mandatory for vector store index operations |
| `NEWS_API_KEY` | Secret (`SecretStr`) | Financial Modeling Prep / AlphaVantage API key | Optional (adapter degrades gracefully if unset) |
| `LANGFUSE_PUBLIC_KEY`| Secret (`SecretStr`) | Langfuse Public Project Key (`pk-lf-...`) | Optional (traces export only if both keys set) |
| `LANGFUSE_SECRET_KEY`| Secret (`SecretStr`) | Langfuse Secret Project Key (`sk-lf-...`) | Optional (traces export only if both keys set) |
| `AUTH_API_KEY` | Secret (`SecretStr`) | High-entropy random hex token (`openssl rand -hex 32`)| Mandatory for API protection when `AUTH_ENABLED=true` |
| `SEC_CONTACT_EMAIL` | String | Valid operator email (e.g. `sec-ops@yourdomain.com`) | Mandatory for live SEC EDGAR full-text queries |

### 2.2 AWS Account & Infrastructure Bootstrap Prerequisites

Execute once per AWS staging account in `us-east-1`:

```bash
# 1. Terraform State S3 Bucket
aws s3api create-bucket --bucket sentinel-tfstate-staging --region us-east-1
aws s3api put-bucket-versioning --bucket sentinel-tfstate-staging --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket sentinel-tfstate-staging \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-public-access-block --bucket sentinel-tfstate-staging \
  --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

# 2. Terraform State Lock DynamoDB Table
aws dynamodb create-table \
  --table-name sentinel-tflock-staging \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1

# 3. AWS Secrets Manager Secret
aws secretsmanager create-secret \
  --name "sentinel-staging-runtime-secrets" \
  --description "Runtime secrets for Sentinel staging environment" \
  --secret-string '{
    "OPENAI_API_KEY": "sk-...",
    "PINECONE_API_KEY": "pcsk_...",
    "NEWS_API_KEY": "...",
    "LANGFUSE_PUBLIC_KEY": "pk-lf-...",
    "LANGFUSE_SECRET_KEY": "sk-lf-...",
    "AUTH_API_KEY": "...",
    "SEC_CONTACT_EMAIL": "ops@yourdomain.com"
  }' \
  --region us-east-1
```

### 2.3 GitHub Actions OIDC & Secrets

| GitHub Setting | Location | Value / Description |
|---|---|---|
| `AWS_ROLE_TO_ASSUME` | Actions Secret | `arn:aws:iam::<ACCOUNT_ID>:role/sentinel-github-actions-staging` |
| `ECR_REGISTRY` | Actions Variable | `<ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com` (optional registry prefix) |
| `AWS_REGION` | Actions Variable | `us-east-1` |
| `staging` Environment | Settings -> Environments | Require manual review approval before deployment |

---

## 3. Local Commands Executed & Verification Results

All automated offline test suites, static analysis, type checking, and contracts passed cleanly:

```bash
# 1. Master release validation suite
make release-check
# Results:
# - Ruff format: 72 files checked, 0 formatting errors
# - Ruff lint: Clean pass (0 violations)
# - Mypy typecheck: 59 Python source files verified (0 type errors)
# - Pytest: 392 backend unit, integration, and security tests passed (100%)
# - ESLint: Frontend clean pass
# - TypeScript (tsc): Clean pass
# - Prettier: All matched files use Prettier code style
# - Vitest: 72 frontend component & integration tests passed across 8 suites
# - Next.js Build: Production standalone bundle compiled cleanly

# 2. Container & Compose contract tests
.venv/bin/python -m pytest backend/tests/test_container_contract.py
# Results:
# - 30 passed in 2.88s (verifies Dockerfile non-root users, unprivileged ports, bounded shutdown, tmpfs, and offline boot)
```

---

## 4. Terraform Plan Summary & Expected AWS Resources

When applied against the staging AWS account with `envs/staging.tfvars`, Terraform provisions **48 cloud resources**:

### Resource Breakdown by Module
1. **Networking (`networking.tf`)**:
   - `aws_vpc.main`: Dedicated VPC (`10.10.0.0/16`)
   - `aws_subnet.public[0..1]`: 2 Multi-AZ public subnets for ALB
   - `aws_subnet.private[0..1]`: 2 Multi-AZ private subnets for ECS tasks
   - `aws_internet_gateway.main`: Ingress/egress gateway
   - `aws_eip.nat[0]`: Dedicated Elastic IP for NAT Gateway
   - `aws_nat_gateway.main[0]`: NAT Gateway in public subnet AZ-A
   - `aws_route_table.public` & `aws_route_table.private`: Route tables with default gateway routes
   - `aws_route_table_association.public[0..1]` & `aws_route_table_association.private[0..1]`
   - `aws_vpc_endpoint.s3[0]`: Gateway endpoint for direct S3 throughput
   - `aws_vpc_endpoint.ecr_api[0]`, `ecr_dkr[0]`, `secretsmanager[0]`, `logs[0]`: Interface endpoints
2. **Security Groups (`security_groups.tf`)**:
   - `aws_security_group.alb`: HTTP/HTTPS ingress
   - `aws_security_group.frontend_ecs`: Port 3000 ingress strictly from ALB
   - `aws_security_group.backend_ecs`: Port 8000 ingress strictly from ALB & Frontend
   - `aws_security_group.vpc_endpoints`: Port 443 ingress from ECS subnets
3. **Secrets Management & IAM (`secrets.tf`, `iam.tf`)**:
   - `aws_secretsmanager_secret.runtime_secrets`: Secrets container with immediate staging recovery window
   - `aws_iam_role.ecs_execution`: Execution role attached to `AmazonECSTaskExecutionRolePolicy`
   - `aws_iam_policy.ecs_execution_secrets`: Scoped policy granting `secretsmanager:GetSecretValue` on runtime secrets
   - `aws_iam_role.ecs_task`: Application task role
4. **Load Balancing & Ingress (`alb.tf`)**:
   - `aws_lb.main`: Internet-facing Application Load Balancer
   - `aws_lb_target_group.frontend`: Target group on port 3000 (`/health`)
   - `aws_lb_target_group.backend`: Target group on port 8000 (`/health`)
   - `aws_lb_listener.http`: Port 80 listener (default forward to Frontend)
   - `aws_lb_listener_rule.backend_direct`: Routing rule for API and docs routes to Backend
5. **Compute & Orchestration (`ecs.tf`)**:
   - `aws_ecs_cluster.main`: ECS cluster with Container Insights enabled
   - `aws_ecs_task_definition.backend`: Fargate task (512 CPU, 1024 MiB RAM) with secret injection
   - `aws_ecs_task_definition.frontend`: Fargate task (256 CPU, 512 MiB RAM)
   - `aws_ecs_service.backend`: Private Fargate service (desired count: 1)
   - `aws_ecs_service.frontend`: Private Fargate service (desired count: 1)
6. **Observability & Alerting (`cloudwatch.tf`)**:
   - `aws_cloudwatch_log_group.backend` & `frontend`: 14-day retention log groups
   - `aws_sns_topic.alerts[0]`: Alert notifications topic
   - `aws_cloudwatch_metric_alarm.backend_cpu_high`: CPU > 80%
   - `aws_cloudwatch_metric_alarm.backend_memory_high`: Memory > 85%
   - `aws_cloudwatch_metric_alarm.alb_5xx_errors`: 5XX errors > 5 / min
   - `aws_cloudwatch_metric_alarm.target_response_time`: p95 latency > 2.0s
   - `aws_cloudwatch_metric_alarm.backend_unhealthy_hosts`: Unhealthy hosts > 0

---

## 5. Post-Deployment Smoke Test Procedures

Execute these tests immediately following a staging apply against the load balancer DNS (`ALB_DNS`):

```bash
ALB="http://<ALB_DNS_NAME>"
AUTH_KEY="<STAGING_AUTH_API_KEY>"

# 1. Frontend Liveness
curl -fsS "$ALB/health"
# Expected: {"status":"ok","service":"sentinel-frontend","version":"0.1.0-rc1"}

# 2. Backend Deep Readiness
curl -fsS "$ALB/ready"
# Expected: {"status":"ready","checks":{"embedding_available":true,"vector_store_ready":true,"providers":["openai"]}}

# 3. Data Source Adapters Status
curl -fsS "$ALB/sources"
# Expected: {"sec_edgar":true,"news_api":true,"apex":false}

# 4. LLM Providers Status
curl -fsS "$ALB/providers"
# Expected: {"available":["openai"]}

# 5. Security & Authentication Enforcement (Negative Test)
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$ALB/query" \
  -H "Content-Type: application/json" \
  -d '{"question":"What was Apple net income?"}'
# Expected: 401 (Unauthorized)

# 6. Authenticated End-to-End Query (Positive Test)
curl -fsS -X POST "$ALB/query" \
  -H "Authorization: Bearer $AUTH_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: staging-smoke-001" \
  -d '{"question":"What was Apple total revenue in fiscal 2024?"}'
# Expected: 200 OK with "answer", "citations", "agent_path", "trace_url"

# 7. Metrics Endpoint Verification
curl -fsS "$ALB/metrics" -H "Authorization: Bearer $AUTH_KEY"
# Expected: 200 OK reporting HTTP request counts and query latencies
```

---

## 6. Rollback Procedures & Failure Recovery

### 6.1 Application / Task Rollback (Unhealthy Revision or Code Regression)
If the new container image exhibits runtime exceptions or fails readiness:

```bash
# 1. Fetch current and previous task definition revisions
aws ecs describe-services \
  --cluster sentinel-staging-ecs-cluster \
  --services sentinel-staging-backend-svc \
  --query "services[0].taskDefinition"

# 2. Roll back Backend service to previous stable revision
aws ecs update-service \
  --cluster sentinel-staging-ecs-cluster \
  --service sentinel-staging-backend-svc \
  --task-definition sentinel-staging-backend:<PREVIOUS_REVISION> \
  --force-new-deployment

# 3. Roll back Frontend service if necessary
aws ecs update-service \
  --cluster sentinel-staging-ecs-cluster \
  --service sentinel-staging-frontend-svc \
  --task-definition sentinel-staging-frontend:<PREVIOUS_REVISION> \
  --force-new-deployment
```

### 6.2 Infrastructure Rollback (Network, ALB, or Security Group Misconfiguration)
If Terraform infrastructure changes cause network partition or routing failures:

```bash
cd infra/terraform

# 1. Check out previous stable tag or commit
git checkout v0.1.0-rc1-stable

# 2. Generate and apply rollback plan
terraform plan -var-file=envs/staging.tfvars -out=rollback.tfplan
terraform apply rollback.tfplan
```

---

## 7. Known Risks, Blockers, and Mitigations

| Risk / Dependency | Impact | Severity | Mitigation |
|---|---|---|---|
| **Missing Secrets Manager Key** | ECS task launch fails | High | Task definitions reference specific JSON keys; verify secret JSON matches schema before launch |
| **Pinecone Index Latency** | First query cold-start delay | Low | Readiness probe (`/ready`) validates vector store connection prior to serving traffic |
| **SEC EDGAR Rate Limit** | Live filing download throttled | Low | `sec_edgar.py` respects 10 req/sec limit and sends valid User-Agent with `SEC_CONTACT_EMAIL` |
| **OpenAI Rate Limit / Quota** | Query fallback or error | Medium | LLM engine implements exponential backoff retry and structured error responses |

---

## 8. Final Deployment Readiness Assessment

### Overall Status: **READY FOR STAGING PLAN REVIEW**

- **Local & Unit Validation**: 100% Passed (392 backend tests, 72 frontend tests, 30 container contract tests, full lint and typechecks).
- **Code & Configuration Quality**: Clean pass; zero unredacted secrets or hardcoded credentials.
- **CI/CD Readiness**: Approval-gated staging pipeline configured with OIDC authentication, immutable image tags, and automated post-deployment health verification.
- **Execution Safeguard**: No live cloud resources have been modified. `terraform apply` requires explicit manual approval in GitHub Actions (`staging` environment) or authorized CLI execution.
