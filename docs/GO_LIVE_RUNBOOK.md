# Sentinel Go-Live & Staging Deployment Runbook

This runbook provides step-by-step operational instructions for provisioning, configuring, deploying, verifying, and recovering Sentinel in staging and production AWS environments.

> [!IMPORTANT]
> **DISCLAIMER & REGULATORY BOUNDARY**:
> Sentinel is an autonomous agentic financial research copilot intended for financial information retrieval, factual extraction, cross-period financial comparison, and cited synthesis. **Sentinel is strictly for research and analysis purposes only and does NOT provide investment, financial, or trading advice.** Sentinel does not execute trades or manage portfolios.

---

## 1. Cloud Prerequisites & Bootstrap

Before executing any Terraform commands or GitHub Actions workflows, initialize the AWS account prerequisites:

### 1.1 Remote State Bootstrap (S3 + DynamoDB)
Run once per AWS account and region (`us-east-1`):

```bash
# 1. Create S3 Bucket for Terraform State
aws s3api create-bucket \
  --bucket sentinel-tfstate-staging \
  --region us-east-1

# 2. Enable Bucket Versioning (Mandatory for state recovery)
aws s3api put-bucket-versioning \
  --bucket sentinel-tfstate-staging \
  --versioning-configuration Status=Enabled

# 3. Enforce Server-Side Encryption (AES256 / AWS KMS)
aws s3api put-bucket-encryption \
  --bucket sentinel-tfstate-staging \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

# 4. Block Public Access on State Bucket
aws s3api put-public-access-block \
  --bucket sentinel-tfstate-staging \
  --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

# 5. Create DynamoDB Table for Distributed State Locking
aws dynamodb create-table \
  --table-name sentinel-tflock-staging \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1
```

### 1.2 AWS Secrets Manager Provisioning
Create the runtime credentials secret container in AWS Secrets Manager:

```bash
aws secretsmanager create-secret \
  --name "sentinel-staging-runtime-secrets" \
  --description "Runtime environment credentials for Sentinel staging" \
  --secret-string '{
    "OPENAI_API_KEY": "sk-proj-...",
    "PINECONE_API_KEY": "pcsk_...",
    "NEWS_API_KEY": "...",
    "LANGFUSE_PUBLIC_KEY": "pk-lf-...",
    "LANGFUSE_SECRET_KEY": "sk-lf-...",
    "AUTH_API_KEY": "sentinel-staging-key-...",
    "SEC_CONTACT_EMAIL": "operator@yourdomain.com"
  }' \
  --region us-east-1
```

### 1.3 GitHub Actions OIDC Authentication Setup
Configure GitHub Actions to authenticate to AWS using OpenID Connect (OIDC) with zero long-lived static AWS access keys:

1. In AWS IAM Console, ensure the Identity Provider for `token.actions.githubusercontent.com` exists.
2. Create an IAM Role `sentinel-staging-github-actions` with trust relationship:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": [
            "repo:kunalparmar/sentinel:environment:staging",
            "repo:kunalparmar/sentinel:ref:refs/heads/main"
          ]
        }
      }
    }
  ]
}
```
3. Set GitHub Environment Secret: `AWS_ROLE_TO_ASSUME = arn:aws:iam::<ACCOUNT_ID>:role/sentinel-staging-github-actions`.

---

## 2. Deployment Procedure

### Method A: CI/CD Pipeline (Recommended)

1. **Push to `main` branch** or trigger `.github/workflows/deploy-staging.yml` via `workflow_dispatch`.
2. **Pre-flight Job**: Automatically executes tests, typechecks, linters, and `terraform validate`.
3. **Plan Job**:
   - Downloads OIDC credentials and initializes Terraform.
   - Generates plan with immutable tag `sentinel-backend:git-<commit_sha>`.
   - Uploads `staging-tfplan` artifact.
4. **Approval Gate**:
   - Open GitHub Repository -> Actions -> Active Workflow.
   - Review plan output in the job logs.
   - Click **Review deployments** -> Select **staging** -> Click **Approve and deploy**.
5. **Apply Job**: Automatically applies the exact archived plan artifact `tfplan`.

### Method B: Manual CLI Deployment

For emergency operations or maintenance by authorized DevOps personnel:

```bash
cd infra/terraform

# 1. Authenticate with AWS CLI
export AWS_PROFILE=sentinel-staging
aws sts get-caller-identity

# 2. Initialize with remote backend
terraform init \
  -backend-config="bucket=sentinel-tfstate-staging" \
  -backend-config="key=sentinel/staging/terraform.tfstate" \
  -backend-config="region=us-east-1" \
  -backend-config="dynamodb_table=sentinel-tflock-staging"

# 3. Create Plan
terraform plan \
  -var-file=envs/staging.tfvars \
  -var="backend_container_image=123456789012.dkr.ecr.us-east-1.amazonaws.com/sentinel-backend:git-$(git rev-parse --short HEAD)" \
  -var="frontend_container_image=123456789012.dkr.ecr.us-east-1.amazonaws.com/sentinel-frontend:git-$(git rev-parse --short HEAD)" \
  -out=tfplan

# 4. Review and Apply
terraform apply tfplan
```

---

## 3. Post-Deployment Verification & Smoke Tests

Run these smoke tests against the provisioned ALB DNS name (`http://<ALB_DNS>`):

### 3.1 Health & Readiness Probes
```bash
ALB="http://<ALB_DNS>"

# Frontend liveness
curl -fsS "$ALB/health"
# {"status":"ok","service":"sentinel-frontend","version":"0.1.0-rc1"}

# Backend readiness (confirms vector store & embedding providers are healthy)
curl -fsS "$ALB/ready"
# {"status":"ready","checks":{"embedding_available":true,"vector_store_ready":true,"providers":["openai"]}}

# Usable data sources
curl -fsS "$ALB/sources"
# {"sec_edgar":true,"news_api":true,"apex":false}
```

### 3.2 Authentication & API Protection Verification
```bash
# 1. Verify unauthenticated query is rejected (401)
curl -s -w "\nHTTP Status: %{http_code}\n" -X POST "$ALB/query" \
  -H "Content-Type: application/json" \
  -d '{"question":"What was Apple revenue in 2024?"}'
# Expect: 401 Unauthorized

# 2. Verify authenticated query succeeds (200)
AUTH_KEY="<YOUR_STAGING_AUTH_API_KEY>"
curl -fsS -X POST "$ALB/query" \
  -H "Authorization: Bearer $AUTH_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: staging-smoke-001" \
  -d '{"question":"What was Apple total net sales in fiscal 2024?"}'
# Expect: 200 OK with answer, citations, and agent_path

# 3. Verify operational metrics snapshot
curl -fsS "$ALB/metrics" -H "Authorization: Bearer $AUTH_KEY"
# Expect: 200 OK with http metrics and query counts
```

---

## 4. Emergency Rollback Procedures

### Scenario 1: Unhealthy Container Image or Application Regression

If the new task definition fails health checks or exhibits runtime errors:

```bash
# 1. Identify current and previous task definition revisions
aws ecs describe-services \
  --cluster sentinel-staging-ecs-cluster \
  --services sentinel-staging-backend-svc \
  --query "services[0].taskDefinition"

# 2. Roll back Backend service to previous revision
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

### Scenario 2: Infrastructure Configuration Failure

If Terraform infrastructure changes caused network or load balancer misconfiguration:

```bash
cd infra/terraform

# 1. Revert Git commit to previous stable release
git checkout <PREVIOUS_STABLE_COMMIT_OR_TAG>

# 2. Re-plan and apply previous infrastructure state
terraform plan -var-file=envs/staging.tfvars -out=rollback_tfplan
terraform apply rollback_tfplan
```

---

## 5. Security & Cost Auditing

### 5.1 Secrets Hygiene
- Ensure no secret values appear in CloudWatch Log Groups (`/sentinel/staging/backend`, `/sentinel/staging/frontend`).
- Confirm `SecretStr` redaction is functioning: grep CloudWatch Logs for any literal `sk-` or `Bearer ` tokens.

### 5.2 Cost Control
- Fargate CPU/Memory sizing in staging is set to `512` CPU / `1024` MiB Memory for backend, `256` CPU / `512` MiB Memory for frontend.
- Single NAT Gateway instance is used in staging to minimize idle charges.
- VPC Endpoints (S3, ECR, Secrets Manager, CloudWatch Logs) bypass NAT traffic for direct AWS internal throughput.
- CloudWatch log retention is capped at 14 days in staging.

---

## 6. Incident Management Contacts (Placeholders)

| Role | Contact Placeholder | Escalation Level |
|---|---|---|
| **Primary DevOps On-Call** | `devops-oncall@example.com` / Slack `#sentinel-alerts` | Level 1 (Immediate) |
| **Backend Engineering Lead** | `backend-lead@example.com` | Level 2 (15 min) |
| **Security Response Team** | `security-incident@example.com` | Level 2 (Immediate on breach) |
| **Executive / Engineering Lead** | `engineering-mgmt@example.com` | Level 3 (30 min) |
