# Sentinel Staging Infrastructure Bootstrap Guide (`v0.1.0-rc1`)

**Document Version**: 1.0.0  
**Target Version**: `v0.1.0-rc1`  
**Target Environment**: AWS Staging (`us-east-1`)  
**Status**: Preflight Bootstrap Audit Complete (Gated for Administrator Execution)  

> [!IMPORTANT]
> **DISCLAIMER & REGULATORY BOUNDARY**:  
> Sentinel is an autonomous agentic financial research copilot designed for information retrieval, structured factual extraction, cross-period financial comparison, and cited synthesis. **Sentinel is strictly for research and analysis purposes only and does NOT provide investment, financial, or trading advice.** Sentinel executes zero trades and does not manage investment portfolios.

---

## 1. Identity & Access Gating (Security Prerequisites)

> [!CAUTION]
> **CRITICAL SECURITY RULES**:
> 1. **NEVER USE ROOT CREDENTIALS**: The AWS Root account identity must NEVER be used to execute Terraform stacks, create staging resources, or configure CI/CD. Root account usage for deployments violates security compliance and will fail preflight gates.
> 2. **NO STATIC LONG-LIVED ACCESS KEYS**: Do not create IAM user access keys (`AKIA...`) or store long-lived credentials in GitHub Secrets. All CI/CD authentication must use short-lived OpenID Connect (OIDC) tokens via AWS STS.
> 3. **LEAST-PRIVILEGE ADMINISTRATIVE ROLE**: Bootstrap actions must be executed exclusively by authorized cloud administrators assuming a dedicated administrative IAM role (e.g. AWS Identity Center / SSO `AdministratorAccess` or a custom `SentinelBootstrapAdmin` role).

### 1.1 Switching from Root to a Least-Privilege IAM Role

If your current AWS CLI environment resolves to `arn:aws:iam::<ACCOUNT_ID>:root`, deployment is **BLOCKED**. Follow these steps to switch to a least-privilege role:

1. **Option A (Recommended — AWS IAM Identity Center / SSO)**:
   ```bash
   # Configure AWS SSO profile
   aws configure sso --profile sentinel-admin
   # SSO session name: sentinel-sso
   # SSO start URL: https://<your-org-sso>.awsapps.com/start
   # SSO region: us-east-1
   # Account ID: <STAGING_ACCOUNT_ID>
   # Role name: AdministratorAccess

   # Log in and set active profile
   aws sso login --profile sentinel-admin
   export AWS_PROFILE=sentinel-admin
   export AWS_REGION=us-east-1
   ```

2. **Option B (Assumed Role via IAM User / Session)**:
   ```bash
   # Assume an authorized bootstrap role
   export $(printf "AWS_ACCESS_KEY_ID=%s AWS_SECRET_ACCESS_KEY=%s AWS_SESSION_TOKEN=%s" \
     $(aws sts assume-role \
       --role-arn "arn:aws:iam::<STAGING_ACCOUNT_ID>:role/SentinelBootstrapAdmin" \
       --role-session-name "sentinel-bootstrap-session" \
       --region us-east-1 \
       --query "Credentials.[AccessKeyId,SecretAccessKey,SessionToken]" \
       --output text))
   ```

3. **Verify Active Non-Root Identity**:
   ```bash
   aws sts get-caller-identity --region us-east-1
   ```
   *Expected Output Verification*: `Arn` must show `assumed-role` or federated user (e.g. `arn:aws:sts::<ACCOUNT_ID>:assumed-role/...`), **never** `:root`.

---

## 2. Missing Prerequisites Audit Matrix

The preflight audit identified the following foundational components required prior to running `terraform apply` on the main application stack (`infra/terraform`):

| # | Missing Prerequisite | Required Identifier | Purpose | Encryption & Retention | Safe to Destroy in Staging? | Managed Via |
|---|---|---|---|---|---|---|
| **1** | **Remote State S3 Bucket** | `sentinel-staging-tfstate-<ACCOUNT_ID>` (or `sentinel-tfstate-staging`) | Stores main Terraform stack state (`sentinel/staging/terraform.tfstate`) | SSE-AES256, Versioning Enabled, Public Access Blocked, 90-day noncurrent version expiration, 7-day multipart upload abort | **NO** (Destruction orphans live cloud resources and loses state) | Bootstrap Stack (`infra/bootstrap/staging-bootstrap.tf`) or CLI Template |
| **2** | **State Lock DynamoDB Table** | `sentinel-staging-tflock` (or `sentinel-tflock-staging`) | Distributed locking for Terraform state operations | SSE Enabled, Pay-Per-Request billing, Point-in-time recovery (PITR) enabled, Partition Key `LockID` (S) | Safe only when no Terraform runs active | Bootstrap Stack or CLI Template |
| **3** | **Backend ECR Repository** | `sentinel-backend` | Stores FastAPI backend Docker images | AES-256 encryption, Immutable tags (`IMMUTABLE`), Scan-on-push enabled, Lifecycle: keep 30 `git-*` tags, expire untagged after 7 days | Yes (if images can be rebuilt from Git commit SHA) | Bootstrap Stack or CLI Template |
| **4** | **Frontend ECR Repository** | `sentinel-frontend` | Stores Next.js frontend Docker images | AES-256 encryption, Immutable tags (`IMMUTABLE`), Scan-on-push enabled, Lifecycle: keep 30 `git-*` tags, expire untagged after 7 days | Yes (if images can be rebuilt from Git commit SHA) | Bootstrap Stack or CLI Template |
| **5** | **GitHub Actions OIDC Provider & Role** | Provider: `token.actions.githubusercontent.com`<br>Role: `sentinel-staging-github-actions` | Authenticates CI/CD without static AWS access keys | Scoped trust policy (`repo:<org>/sentinel:environment:staging`), Granular staging infrastructure permissions | Safe to recreate, breaks CI/CD while missing | Bootstrap Stack or CLI Template |
| **6** | **Runtime Secrets Manager Secret** | `sentinel-staging-runtime-secrets` | Secret container for backend application credentials | AWS KMS (`aws/secretsmanager`), 0-day recovery window in staging (immediate deletion on destroy) | Safe in staging (must repopulate JSON payload upon recreation) | Main Stack (`infra/terraform/secrets.tf`) + Out-of-band CLI population |
| **7** | **GitHub Protected Environment** | Environment: `staging` | Approval gate and deployment boundary | Environment secrets (`AWS_ROLE_TO_ASSUME`), variables (`AWS_REGION`, `ECR_REGISTRY`), required reviewers | N/A (GitHub settings) | GitHub UI / `gh` CLI |
| **8** | **Release Container Images** | `sentinel-backend:git-<commit_sha>`<br>`sentinel-frontend:git-<commit_sha>` | Immutable images built from release candidate commit | Multi-stage build, non-root execution (UID 10001 / UID 1000), hermetic tests passed | Yes (reproducible from Git SHA) | Docker CLI / CI Pipeline |

---

## 3. Safe Bootstrap Sequence (Step-by-Step)

The bootstrap sequence must be executed in the following strict chronological order:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 1: Verify Non-Root Administrative AWS Identity                         │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 2: Deploy Bootstrap Stack (`infra/bootstrap/staging-bootstrap.tf`)     │
│         - Creates S3 State Bucket, DynamoDB Lock Table                      │
│         - Creates Backend & Frontend ECR Repositories                       │
│         - Creates OIDC Provider & Staging Deployment Role                   │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 3: Configure GitHub Environment (`staging`) & Secrets                  │
│         - Set `AWS_ROLE_TO_ASSUME`, `AWS_REGION`, `ECR_REGISTRY`            │
│         - Configure Required Reviewers for Manual Approval Gate             │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 4: Build & Push Release Images to ECR                                  │
│         - Tag: `git-<commit_sha>` (Immutable)                               │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 5: Populate Runtime Secrets in AWS Secrets Manager                     │
│         - Set API keys & SEC contact email out-of-band                      │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 6: Run Main Terraform Plan & Review Approval Gate                      │
│         - `infra/terraform` initialized against remote S3 backend           │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Bootstrap Execution Templates (Templates with Placeholders)

### 4.1 Method A: Using the Bootstrap Terraform Stack (Recommended)

```bash
cd infra/bootstrap

# 1. Format and initialize
terraform fmt -check
terraform init

# 2. Plan bootstrap resources
terraform plan \
  -var="project_name=sentinel" \
  -var="environment=staging" \
  -var="aws_region=us-east-1" \
  -var="github_repository=<YOUR_GITHUB_ORG_OR_USER>/sentinel" \
  -var="create_oidc_provider=true" \
  -out=bootstrap.tfplan

# 3. Apply reviewed plan
terraform apply bootstrap.tfplan
```

### 4.2 Method B: Manual AWS CLI Bootstrap (Alternative)

If provisioning foundational prerequisites manually using AWS CLI:

```bash
ACCOUNT_ID="<YOUR_AWS_ACCOUNT_ID>"
REGION="us-east-1"
BUCKET_NAME="sentinel-staging-tfstate-${ACCOUNT_ID}"
LOCK_TABLE="sentinel-staging-tflock"
GITHUB_REPO="<YOUR_GITHUB_ORG_OR_USER>/sentinel"

# 1. S3 State Bucket
aws s3api create-bucket \
  --bucket "$BUCKET_NAME" \
  --region "$REGION"

aws s3api put-bucket-versioning \
  --bucket "$BUCKET_NAME" \
  --versioning-configuration Status=Enabled \
  --region "$REGION"

aws s3api put-bucket-encryption \
  --bucket "$BUCKET_NAME" \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' \
  --region "$REGION"

aws s3api put-public-access-block \
  --bucket "$BUCKET_NAME" \
  --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" \
  --region "$REGION"

# 2. DynamoDB Lock Table
aws dynamodb create-table \
  --table-name "$LOCK_TABLE" \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region "$REGION"

# 3. ECR Repositories
aws ecr create-repository \
  --repository-name "sentinel-backend" \
  --image-tag-mutability IMMUTABLE \
  --image-scanning-configuration scanOnPush=true \
  --encryption-configuration encryptionType=AES256 \
  --region "$REGION"

aws ecr create-repository \
  --repository-name "sentinel-frontend" \
  --image-tag-mutability IMMUTABLE \
  --image-scanning-configuration scanOnPush=true \
  --encryption-configuration encryptionType=AES256 \
  --region "$REGION"

# 4. GitHub Actions OIDC Role
aws iam create-role \
  --role-name "sentinel-staging-github-actions" \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Principal": {
          "Federated": "arn:aws:iam::'"$ACCOUNT_ID"':oidc-provider/token.actions.githubusercontent.com"
        },
        "Action": "sts:AssumeRoleWithWebIdentity",
        "Condition": {
          "StringEquals": {
            "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
          },
          "StringLike": {
            "token.actions.githubusercontent.com:sub": [
              "repo:'"$GITHUB_REPO"':environment:staging",
              "repo:'"$GITHUB_REPO"':ref:refs/heads/main"
            ]
          }
        }
      }
    ]
  }'
```

---

## 5. Runtime Secrets Schema & Population

The runtime secrets container `sentinel-staging-runtime-secrets` is provisioned by Terraform (`infra/terraform/secrets.tf`). Its secret values are injected into ECS Fargate containers at runtime.

### 5.1 JSON Payload Schema (Placeholders Only — Never Commit Secrets)

```json
{
  "OPENAI_API_KEY": "sk-proj-...",
  "PINECONE_API_KEY": "pcsk_...",
  "NEWS_API_KEY": "...",
  "LANGFUSE_PUBLIC_KEY": "pk-lf-...",
  "LANGFUSE_SECRET_KEY": "sk-lf-...",
  "AUTH_API_KEY": "...",
  "SEC_CONTACT_EMAIL": "sec-ops@yourdomain.com"
}
```

### 5.2 Key Requirements & Validation Rules

| Key Name | Requirement | Validation Gating | Format / Constraints |
|---|---|---|---|
| `OPENAI_API_KEY` | **Mandatory** | `production_blockers()` check | OpenAI API key (`sk-proj-...` or `sk-...`) |
| `PINECONE_API_KEY` | **Mandatory** | `production_blockers()` check | Pinecone Serverless API key (`pcsk_...`) |
| `NEWS_API_KEY` | Optional | Degrades gracefully if blank | Financial Modeling Prep or AlphaVantage token |
| `LANGFUSE_PUBLIC_KEY`| Optional | Traces export only if both keys set | Langfuse project public key (`pk-lf-...`) |
| `LANGFUSE_SECRET_KEY`| Optional | Traces export only if both keys set | Langfuse project secret key (`sk-lf-...`) |
| `AUTH_API_KEY` | **Mandatory** when `AUTH_ENABLED=true` | Blocked if auth enabled without key | High-entropy 32-byte hex token (`openssl rand -hex 32`) |
| `SEC_CONTACT_EMAIL` | **Mandatory** | `is_placeholder_contact_email()` rejects `example.com` domains | Valid operator email address (e.g. `sec-ops@yourcompany.com`) |

### 5.3 Safe Out-of-Band Population via CLI

```bash
# Generate high-entropy API key for Sentinel auth
AUTH_KEY=$(openssl rand -hex 32)

# Populate secret values safely without printing to stdout
aws secretsmanager put-secret-value \
  --secret-id "sentinel-staging-runtime-secrets" \
  --secret-string '{
    "OPENAI_API_KEY": "'"$STAGING_OPENAI_API_KEY"'",
    "PINECONE_API_KEY": "'"$STAGING_PINECONE_API_KEY"'",
    "NEWS_API_KEY": "'"${STAGING_NEWS_API_KEY:-}"'",
    "LANGFUSE_PUBLIC_KEY": "'"${STAGING_LANGFUSE_PUBLIC_KEY:-}"'",
    "LANGFUSE_SECRET_KEY": "'"${STAGING_LANGFUSE_SECRET_KEY:-}"'",
    "AUTH_API_KEY": "'"$AUTH_KEY"'",
    "SEC_CONTACT_EMAIL": "sec-ops@yourdomain.com"
  }' \
  --region us-east-1
```

---

## 6. Container Image Build & Push Sequence

Release container images must be built from the target release commit SHA and pushed to ECR before running `terraform apply`.

```bash
ACCOUNT_ID="<YOUR_AWS_ACCOUNT_ID>"
REGION="us-east-1"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
COMMIT_SHA=$(git rev-parse --short HEAD)
TAG="git-${COMMIT_SHA}"

# 1. Authenticate Docker with AWS ECR
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"

# 2. Build backend production image
docker build -f infra/Dockerfile.backend --target production \
  -t "${REGISTRY}/sentinel-backend:${TAG}" \
  -t "${REGISTRY}/sentinel-backend:staging-latest" .

# 3. Build frontend production image
docker build -f infra/Dockerfile.frontend --target production \
  -t "${REGISTRY}/sentinel-frontend:${TAG}" \
  -t "${REGISTRY}/sentinel-frontend:staging-latest" .

# 4. Push images to ECR
docker push "${REGISTRY}/sentinel-backend:${TAG}"
docker push "${REGISTRY}/sentinel-backend:staging-latest"
docker push "${REGISTRY}/sentinel-frontend:${TAG}"
docker push "${REGISTRY}/sentinel-frontend:staging-latest"
```

---

## 7. Verification Commands (Zero Secret Leakage)

Verify all bootstrap prerequisites without exposing sensitive values:

```bash
ACCOUNT_ID="<YOUR_AWS_ACCOUNT_ID>"
REGION="us-east-1"

# 1. Verify S3 State Bucket exists and is private
aws s3api get-public-access-block \
  --bucket "sentinel-staging-tfstate-${ACCOUNT_ID}" \
  --region "$REGION"

# 2. Verify DynamoDB Lock Table
aws dynamodb describe-table \
  --table-name "sentinel-staging-tflock" \
  --region "$REGION" \
  --query "Table.TableStatus"

# 3. Verify ECR Repositories & Pushed Images
aws ecr list-images --repository-name sentinel-backend --region "$REGION"
aws ecr list-images --repository-name sentinel-frontend --region "$REGION"

# 4. Verify Secrets Manager Secret Structure (Metadata only, no secrets printed)
aws secretsmanager describe-secret \
  --secret-id "sentinel-staging-runtime-secrets" \
  --region "$REGION" \
  --query "[Name, ARN, LastChangedDate]"

# 5. Verify GitHub Actions OIDC Role Trust
aws iam get-role \
  --role-name "sentinel-staging-github-actions" \
  --query "Role.AssumeRolePolicyDocument"
```

---

## 8. Recovery, Rollback, and State Protection Guidance

| Scenario | Impact | Mitigation / Recovery Procedure |
|---|---|---|
| **Accidental State File Corruption** | Terraform cannot reconcile state | S3 Bucket Versioning is enabled. Restore the previous S3 object version: `aws s3api get-object --bucket sentinel-staging-tfstate-<ACCOUNT_ID> --key sentinel/staging/terraform.tfstate --version-id <VERSION_ID> recovered.tfstate && aws s3 cp recovered.tfstate s3://sentinel-staging-tfstate-<ACCOUNT_ID>/sentinel/staging/terraform.tfstate`. |
| **Stale DynamoDB Lock** | Terraform execution blocked with lock error | Verify no pipeline or engineer is actively running, then force unlock using lock ID: `terraform force-unlock <LOCK_ID>`. |
| **Missing Secrets Key at Task Launch** | ECS Fargate tasks fail health checks | Check CloudWatch log stream `/sentinel/staging/backend` for startup validation errors. Populate missing key in `sentinel-staging-runtime-secrets` and restart tasks: `aws ecs update-service --cluster sentinel-staging-ecs-cluster --service sentinel-staging-backend-svc --force-new-deployment`. |
| **ECR Image Tag Collision** | Image push rejected | Image tags are immutable. Ensure new builds use distinct commit SHAs (`git-<sha>`). Never force-push or overwrite release tags. |
