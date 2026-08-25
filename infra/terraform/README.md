# Sentinel — Terraform skeleton (AWS target)

**Status: deployment-neutral SKELETON.** Version pins, validated variables,
naming/tagging conventions, and clearly-marked module extension points.
`main.tf` defines **zero resources** — nothing here has ever been applied to
any AWS account, and `terraform apply` is a no-op by construction.

## Files

| File | Purpose |
|---|---|
| `versions.tf` | Terraform `>= 1.9, < 2` pin; AWS provider `~> 6.0`; EXAMPLE (commented) S3 remote-state block |
| `providers.tf` | AWS provider wired to `var.aws_region` + baseline default tags; credentials come from the environment only |
| `variables.tf` | Every deployment input, with validation (environment allow-list, region shape, Fargate CPU steps, …) |
| `main.tf` | `locals` (`name_prefix`, `tags`) + commented extension-point modules: network, secrets, IAM, hosting, logging, monitoring |
| `outputs.tf` | Values derivable today (`name_prefix`, `environment`, `baseline_tags`) |

## Extension points → planned modules

1. **Networking** — private-subnet VPC + VPC endpoints (ECR/S3/Secrets Manager/Logs).
2. **Secrets management** — one Secrets Manager secret per environment; the ECS
   execution role reads it at task start. Runtime credentials never live in
   tfvars, task definitions, or git.
3. **IAM** — least-privilege execution + task roles.
4. **Container hosting** — ECS Fargate running the CI-built image from
   `var.container_image`. v1 has no application auth (spec §17): the service
   must NOT be internet-exposed until that changes.
5. **Logging** — CloudWatch log group with `var.log_retention_days`.
6. **Monitoring** — alarms + SNS, gated on `var.enable_monitoring`.

Implement them as small modules under `modules/<name>` in that order — each
takes `name_prefix` + `tags` and exports only what later blocks need.

## Workflow (no cloud credentials required for 1–3)

```bash
cd infra/terraform

terraform fmt -recursive                      # 1. formatting gate (CI too)
terraform init -backend=false                 # 2. provider download, no state
terraform validate                            # 3. structural validation

# When resources exist and you intend to change infrastructure:
terraform plan -var-file=envs/dev.tfvars      # 4. preview ONLY
terraform apply -var-file=envs/dev.tfvars     # 5. explicit, reviewed change
```

### Environment separation

One root module, three tfvars entry points (create under `envs/`, git-ignorable
via `*.tfvars` — keep an `*.tfvars.example` copy if useful):

```hcl
# envs/dev.tfvars
environment        = "dev"
aws_region         = "us-east-1"
desired_count      = 1
log_retention_days = 7
```

Staging/prod differ by tfvars only (`staging.tfvars`, `prod.tfvars`); remote
state keys isolate them (`sentinel/staging/...`, `sentinel/prod/...`). Never
share a workspace or state file across environments.

### Remote state bootstrap (manual, once)

The commented `backend "s3"` block in `versions.tf` is an EXAMPLE. To enable:

1. Create (manually or via a separate bootstrap config) the state bucket with
   versioning + SSE enabled and a DynamoDB lock table.
2. Uncomment the block, replace `EXAMPLE-*` placeholders, set the per-
   environment `key`.
3. `terraform init` and commit the resulting `.terraform.lock.hcl` (lock files
   ARE committed; state files NEVER are — enforced by `.gitignore`).

## Secret rules

- No credentials anywhere in this directory — not in code, tfvars, examples,
  or git history. Authenticate via `AWS_PROFILE`/SSO; runtime app secrets go
  through Secrets Manager once that module exists.
- State files can contain sensitive values: they are gitignored and must land
  only in the encrypted remote backend.

## Explicitly out of scope of this skeleton

No VPC, no cluster, no roles, no buckets, no alarms exist because of this
directory. The first real provisioning task must say so in its own PR/review
and run against a dev account explicitly chosen by the owner.
