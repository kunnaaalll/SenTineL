# Sentinel — Infrastructure Bootstrap Stack (AWS)

## Overview

The **Sentinel Bootstrap Stack** (`infra/bootstrap/staging-bootstrap.tf`) provisions the foundational AWS resources required **before** the main application infrastructure stack can be initialized or applied.

```
                         ┌────────────────────────────────────────────────────────┐
                         │              Sentinel AWS Staging Account              │
                         │                                                        │
                         │   ┌────────────────────────────────────────────────┐   │
                         │   │   1. S3 State Storage                          │   │
                         │   │   Bucket: sentinel-staging-tfstate-<ACCOUNT_ID>│   │
                         │   │   (SSE-AES256, Versioning, Private, Lifecycle) │   │
                         │   └────────────────────────────────────────────────┘   │
                         │                                                        │
                         │   ┌────────────────────────────────────────────────┐   │
                         │   │   2. DynamoDB Lock Table                       │   │
                         │   │   Table: sentinel-staging-tflock               │   │
                         │   │   (Pay-Per-Request, HASH: LockID, PITR)        │   │
                         │   └────────────────────────────────────────────────┘   │
                         │                                                        │
                         │   ┌────────────────────────────────────────────────┐   │
                         │   │   3. ECR Container Repositories                │   │
                         │   │   - sentinel-backend                           │   │
                         │   │   - sentinel-frontend                          │   │
                         │   │   (Immutable Tags, Scan-on-Push, Lifecycle)    │   │
                         │   └────────────────────────────────────────────────┘   │
                         │                                                        │
┌────────────────────┐   │   ┌────────────────────────────────────────────────┐   │
│ GitHub Actions CI  ├───┼──►│   4. IAM OIDC Provider & Deployment Role       │   │
│ (Short-Lived Token)│   │   │   Role: sentinel-staging-github-actions        │   │
│                    │   │   │   (Scoped Trust: repo:owner/repo:env:staging)  │   │
└────────────────────┘   │   └────────────────────────────────────────────────┘   │
                         │                                                        │
                         └────────────────────────────────────────────────────────┘
```

> [!CAUTION]
> **CRITICAL SECURITY RULES**:
> 1. **NEVER USE ROOT CREDENTIALS**: Root account access must NEVER be used to apply Terraform or deploy application components.
> 2. **NO STATIC AWS KEYS**: Do not generate long-lived IAM access keys or store static AWS credentials in GitHub Secrets.
> 3. **APPROVAL-GATED**: This bootstrap stack is provisioned once per environment by an authorized cloud administrator.

---

## Provisioned Resources

| Resource | Identifier / Name Pattern | Purpose | Security & Lifecycle Controls |
|---|---|---|---|
| **S3 State Bucket** | `sentinel-staging-tfstate-<ACCOUNT_ID>` | Remote Terraform state storage | AES-256 encryption, Bucket versioning enabled, Public access completely blocked, 90-day noncurrent version expiration, 7-day multipart upload abort |
| **DynamoDB Lock Table**| `sentinel-staging-tflock` | Distributed state locking | Pay-Per-Request billing, Point-in-time recovery (PITR) enabled, Server-side encryption enabled, Primary Key `LockID` (String) |
| **Backend ECR Repo** | `sentinel-backend` | Docker images for FastAPI backend | Immutable image tags, Scan-on-push enabled, AES-256 encryption, Lifecycle: retain last 30 release tags (`git-*`), expire untagged after 7 days |
| **Frontend ECR Repo** | `sentinel-frontend` | Docker images for Next.js frontend | Immutable image tags, Scan-on-push enabled, AES-256 encryption, Lifecycle: retain last 30 release tags (`git-*`), expire untagged after 7 days |
| **IAM OIDC Provider** | `token.actions.githubusercontent.com` | OpenID Connect federation for GitHub | Audience: `sts.amazonaws.com`, Thumbprints: SHA-1 root CAs |
| **Deployment IAM Role**| `sentinel-staging-github-actions` | Role assumed by GitHub Actions | Scoped trust policy (`repo:owner/repo:environment:staging`), Granular staging permissions (VPC, ALB, ECS, Secrets Manager metadata, CloudWatch) |

---

## Safe Execution Workflow

### Step 1: Assume an Authorized Administrative IAM Role (Not Root)

Authenticate using your organization's IAM Identity Center (AWS SSO) or assumed administrator role:

```bash
# Authenticate via AWS SSO / Identity Center
aws sso login --profile sentinel-admin

# Export active profile
export AWS_PROFILE=sentinel-admin
export AWS_REGION=us-east-1

# Verify identity is an IAM role / federated user (NOT root)
aws sts get-caller-identity
```

Expected output format:
```json
{
    "UserId": "AROA...:user.name",
    "Account": "123456789012",
    "Arn": "arn:aws:sts::123456789012:assumed-role/AWSReservedSSO_AdministratorAccess_.../user.name"
}
```

> [!WARNING]
> If `Arn` ends with `:root`, **STOP IMMEDIATELY**. Switch to an administrative IAM role before proceeding.

---

### Step 2: Validate and Plan the Bootstrap Stack

```bash
cd infra/bootstrap

# 1. Format check
terraform fmt -check

# 2. Initialize provider plugins (local state)
terraform init

# 3. Generate execution plan
terraform plan \
  -var="project_name=sentinel" \
  -var="environment=staging" \
  -var="aws_region=us-east-1" \
  -var="github_repository=<YOUR_GITHUB_ORG_OR_USER>/sentinel" \
  -var="create_oidc_provider=true" \
  -out=bootstrap.tfplan
```

Review the plan: exactly **14 resources** should be scheduled for creation.

---

### Step 3: Apply the Bootstrap Stack (One-Time Execution)

```bash
# Execute the reviewed plan
terraform apply bootstrap.tfplan
```

Capture the outputs displayed by Terraform:
- `s3_tfstate_bucket`: e.g. `sentinel-staging-tfstate-123456789012`
- `dynamodb_tflock_table`: `sentinel-staging-tflock`
- `ecr_backend_repository_url`: `123456789012.dkr.ecr.us-east-1.amazonaws.com/sentinel-backend`
- `ecr_frontend_repository_url`: `123456789012.dkr.ecr.us-east-1.amazonaws.com/sentinel-frontend`
- `github_actions_role_arn`: `arn:aws:iam::123456789012:role/sentinel-staging-github-actions`

---

### Step 4: Configure GitHub Environment and Secrets

In your GitHub repository settings:

1. Navigate to **Settings** -> **Environments** -> **New Environment** -> `staging`.
2. Configure **Deployment protection rules**:
   - Check **Required reviewers** -> Select release managers / DevOps leads.
   - Check **Deployment branches** -> Limit to `main`.
3. Add **Environment secrets**:
   - `AWS_ROLE_TO_ASSUME`: `<github_actions_role_arn>` (from Step 3 output).
4. Add **Environment variables**:
   - `AWS_REGION`: `us-east-1`
   - `ECR_REGISTRY`: `<ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com`

---

### Step 5: Configure Main Stack Backend (`infra/terraform/`)

Initialize the main application Terraform stack against the new remote backend:

```bash
cd ../terraform

# Initialize with the bootstrapped S3 bucket and DynamoDB table
terraform init \
  -backend-config="bucket=<s3_tfstate_bucket>" \
  -backend-config="key=sentinel/staging/terraform.tfstate" \
  -backend-config="region=us-east-1" \
  -backend-config="dynamodb_table=sentinel-staging-tflock"
```

---

## Rollback & State Destruction Safeguards

- **State S3 Bucket**: S3 bucket versioning is enabled with MFA delete support. The bucket must never be deleted while live resources exist.
- **DynamoDB Lock Table**: Point-in-time recovery ensures lock table definitions can be restored if corrupted.
- **ECR Repositories**: Immutable tags prevent accidental overwrite of existing production or release candidate images (`git-<commit_sha>`).
