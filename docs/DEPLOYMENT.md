# Sentinel — Deployment & Operations

Covers containerization, the local Compose stack, environment configuration, health/readiness semantics, the Terraform staging infrastructure, secret management, CI gates, and approval-gated deployments.

---

## 1. Local Development (No Containers)

```bash
make setup            # .venv from backend/requirements-dev.txt
cp .env.example .env  # optional — empty file is a valid offline config
make run              # uvicorn on 127.0.0.1:8000
make check            # ruff format/lint + mypy + offline test suite
```

`backend/` is a **source root** (absolute imports like `from models.schemas import Chunk`). Tooling resolves it via `pythonpath` in `pyproject.toml`, `PYTHONPATH=backend`, and inside images via `PYTHONPATH=/app/backend`. There are no runtime `sys.path` hacks.

---

## 2. Container Images

### Backend (`infra/Dockerfile.backend`)
- **Base**: `python:3.11-slim-trixie` (pinned tag).
- **User**: Unprivileged `sentinel:sentinel` (UID 10001).
- **Filesystem**: Runs safely under read-only root with writable `/tmp`.
- **Runtime**: `uvicorn api.main:app --host 0.0.0.0 --port 8000` with 20s graceful shutdown bound.
- **Healthcheck**: `GET /health` on loopback.

```bash
docker build -f infra/Dockerfile.backend --target production -t sentinel-backend:local .
```

### Frontend (`infra/Dockerfile.frontend`)
- **Base**: `node:20-alpine` (pinned tag).
- **User**: Unprivileged `node:node` (UID 1000).
- **Runtime**: Next.js standalone server on port 3000.
- **Backend Routing**: Proxying configured via `BACKEND_ORIGIN` environment variable.
- **Healthcheck**: `GET /health` on port 3000.

```bash
docker build -f infra/Dockerfile.frontend --target production -t sentinel-frontend:local .
```

---

## 3. Docker Compose Stack

`infra/docker-compose.yml` provides a standalone stack containing both `backend` and `frontend` on a private bridge network (`sentinel`).

```bash
docker compose -f infra/docker-compose.yml up --build
curl -s localhost:8000/health   # {"status":"ok",...}
curl -s localhost:3000/health   # {"status":"ok","service":"sentinel-frontend"}
```

- **Backend**: Loopback-bound (`127.0.0.1:8000`), read-only root filesystem, tmpfs `/tmp`, `cap_drop: ALL`.
- **Frontend**: Loopback-bound (`127.0.0.1:3000`), non-root `node` user, internal communication to `http://backend:8000`.

---

## 4. Terraform Staging Infrastructure (`infra/terraform/`)

The infrastructure is codified in Terraform targeting AWS (AWS provider `~> 6.0`, Terraform `>= 1.9`).

### Architecture Summary

```
                       [ Internet ]
                            |
                            v (Port 80/443)
              [ Application Load Balancer ]
               (Multi-AZ Public Subnets)
                            |
         +------------------+------------------+
         | Path: /* (Default)                  | Path: /query, /agents/*, /ingest, /sources,
         v (Port 3000)                         |       /providers, /ready, /metrics, /docs...
[ Frontend ECS Fargate ]                       v (Port 8000)
 (Private Subnets)                    [ Backend ECS Fargate ]
         |                             (Private Subnets)
         +------------------+------------------+
                            |
                            v
       [ VPC Endpoints & NAT Gateway ]
        - AWS Secrets Manager (/sentinel/staging/runtime-secrets)
        - CloudWatch Logs (/sentinel/staging/backend, /frontend)
        - ECR (api, dkr) & S3 Gateway
```

### Resource Inventory (32 Codified Resources)
1. **Networking (`networking.tf`)**: VPC (`10.0.0.0/16`), 2 public subnets, 2 private subnets, Internet Gateway, NAT Gateway (with EIP), route tables, and VPC Endpoints for S3, ECR, Secrets Manager, and CloudWatch.
2. **Security Groups (`security_groups.tf`)**: Strict least-privilege ingress (ALB on 80/443; Frontend on 3000 from ALB only; Backend on 8000 from ALB & Frontend only; Endpoints on 443 from ECS tasks).
3. **Secrets Management (`secrets.tf`)**: AWS Secrets Manager secret `/sentinel/${name_prefix}/runtime-secrets` with environment-aware deletion protection.
4. **IAM Roles (`iam.tf`)**: ECS Task Execution Role with `secretsmanager:GetSecretValue` and CloudWatch logging permissions; ECS Task Role.
5. **Load Balancing (`alb.tf`)**: Application Load Balancer with frontend (port 3000) and backend (port 8000) target groups, HTTP listener, and path-based routing for API and documentation endpoints.
6. **ECS Fargate Compute (`ecs.tf`)**: Cluster with Container Insights, Backend task definition injecting secrets from Secrets Manager, Frontend task definition, and Fargate services.
7. **Observability & Alarms (`cloudwatch.tf`)**: CloudWatch log groups, metric alarms for CPU, Memory, ALB 5XX errors, Target Latency, and Unhealthy hosts, with SNS notification topic.

### Remote State Setup (S3 + DynamoDB)

State locking and encryption are mandatory:
```bash
aws s3api create-bucket --bucket sentinel-tfstate-staging --region us-east-1
aws s3api put-bucket-encryption --bucket sentinel-tfstate-staging \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-bucket-versioning --bucket sentinel-tfstate-staging \
  --versioning-configuration Status=Enabled

aws dynamodb create-table \
  --table-name sentinel-tflock-staging \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1
```

---

## 5. Deployment Workflow & Release Controls

### Staging Deployment (`.github/workflows/deploy-staging.yml`)
- Triggered on push to `main` or via manual `workflow_dispatch`.
- Executes automated pre-flight checks (pytest, mypy, ruff, terraform validate) before plan generation.
- Authenticates using short-lived OIDC IAM credentials (`token.actions.githubusercontent.com`).
- Tags images immutably with commit SHA (`git-${{ github.sha }}`).
- Generates and archives a Terraform plan artifact (`staging-tfplan`).
- Requires explicit GitHub Environment approval for `staging` before executing apply.
- Updates ECS Fargate tasks with rolling blue/green deployments.

### Operational Runbooks & Checklists
- **Release Checklist**: [RELEASE_CHECKLIST.md](file:///Applications/My%20Mac/Development/Projects/SenTineL/docs/RELEASE_CHECKLIST.md)
- **Go-Live Runbook**: [GO_LIVE_RUNBOOK.md](file:///Applications/My%20Mac/Development/Projects/SenTineL/docs/GO_LIVE_RUNBOOK.md)

### Rollback Procedure
If a deployment degrades or triggers CloudWatch alarms:
1. Revert to the previous stable container image tag or task revision:
   ```bash
   aws ecs update-service \
     --cluster sentinel-staging-ecs-cluster \
     --service sentinel-staging-backend-svc \
     --task-definition sentinel-staging-backend:<PREVIOUS_REVISION>
   ```
2. Invalidate CDN / load balancer caches if needed.
3. If infrastructure changes are broken, revert Git commit and run:
   ```bash
   terraform apply -var-file=envs/staging.tfvars
   ```

---

## 6. Render Cold-Start Behavior & Readiness Polling Contract

When deployed to hosting platforms with scale-to-zero / idle suspend characteristics (such as Render free/hobby tiers), backend spin-up typically requires 50–90 seconds.

### Cold-Start Contract:
1. **Initial Probing**: On first browser load, the frontend makes a single immediate call to `GET /api/ready`.
2. **Immediate Passthrough**: If the backend responds with HTTP `200` (`status: "ready"`), `BackendGate` renders the application shell immediately with zero delay, animations, or splash screens.
3. **Graceful Wake-Up Screen**: If the backend returns `502 Bad Gateway`, `503 Service Unavailable`, `504 Gateway Timeout`, connection reset, or network timeout:
   - The UI mounts a dedicated, calm wake-up screen: *"Namaste, welcome to Sentinel. The research engine is starting. This usually takes about one minute."*
   - Real-time elapsed time counter and progress pulse.
   - Bounded polling initiates: begins at 2s, increases to 5s, capped strictly at 120 seconds total.
4. **Smooth Transition**: Upon the first HTTP 200 `/api/ready` confirmation, the gate automatically transitions into the research interface.
5. **Timeout Handling**: If 120 seconds elapse without readiness, polling halts to prevent infinite loops, displaying an actionable Retry button and a collapsible Technical Details panel.
6. **Session-Loss Protection**: If the backend goes down during active usage, the UI displays a non-blocking degraded alert at the top without discarding loaded messages or in-memory state.
7. **Zero Secret Leakage**: Raw backend error responses, provider credentials, and stack traces are completely masked from user-facing error states.

---

## 7. Local Browser-Only Chat Persistence

To support user workflow continuity across page reloads without backend session stores or accounts:

- **Storage Key**: `sentinel.chat.v1` in `localStorage`.
- **Payload Structure**:
  ```json
  {
    "version": 1,
    "savedAt": "2026-08-30T10:00:00.000Z",
    "messages": [
      {
        "id": "msg-1",
        "role": "user",
        "content": "Compare AAPL and MSFT revenue",
        "timestamp": "2026-08-30T10:00:00.000Z"
      },
      {
        "id": "msg-2",
        "role": "assistant",
        "content": "...",
        "citations": [...],
        "agentPath": ["classify", "fetch", "extract", "compare", "synthesize"],
        "timestamp": "2026-08-30T10:00:05.000Z"
      }
    ]
  }
  ```
- **Hydration Safety**: Stored messages are loaded in a `useEffect` hook post-mount, guaranteeing zero SSR-to-client DOM divergence or hydration mismatch errors.
- **Bounds & Quota**: Bounded to a 50-message FIFO buffer. Quota exceptions and corrupted JSON are safely trapped and logged to console, maintaining in-memory chat functionality.
- **In-Flight Safety**: In-flight requests are excluded from storage; only finalized questions and answers are persisted.
- **Clear Action**: A dedicated "Clear conversation" button with an accessible modal allows instant local deletion.

---

## 8. Privacy & Data Boundary Limitations

1. **Client-Side Isolation**: Conversation histories reside entirely within the individual browser's `localStorage`. No chat transcripts, prompts, or user history are persisted on the backend database.
2. **Credential Exclusion**: Secrets, API keys, Authorization headers, and Langfuse tokens are never written to browser storage or exposed in frontend code.
3. **Single-Device Scope**: Saved conversations do not sync across devices or browser profiles. Clearing browser data permanently purges saved sessions.

