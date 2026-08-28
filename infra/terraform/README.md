# Sentinel — Terraform Infrastructure (AWS)

Production-ready AWS infrastructure definitions for staging and production environments.

## Architecture Overview

```
                      INTERNET
                         │
                         ▼
        ┌──────────────────────────────────┐
        │ Application Load Balancer (ALB)  │ (Public Subnets: 10.0.1.0/24, 10.0.2.0/24)
        │   Port 80 (HTTP) / 443 (HTTPS)   │
        └──────────────┬───────────────────┘
                       │
       ┌───────────────┴───────────────┐
       │ Default                       │ Path: /backend/*, /query, /ingest...
       ▼                               ▼
┌───────────────┐               ┌───────────────┐
│ Next.js UI    │               │ FastAPI API   │ (Private Subnets: 10.0.10.0/24, 10.0.11.0/24)
│ Fargate (3000)│               │ Fargate (8000)│
└───────────────┘               └───────┬───────┘
                                        │
           ┌────────────────────────────┼────────────────────────────┐
           ▼                            ▼                            ▼
  ┌─────────────────┐          ┌─────────────────┐          ┌─────────────────┐
  │ Secrets Manager │          │ CloudWatch Logs │          │ VPC Endpoints   │
  │ Runtime Secrets │          │ Alarms & Metrics│          │ ECR, S3, Logs   │
  └─────────────────┘          └─────────────────┘          └─────────────────┘
```

## Module Structure

| File | Purpose | Resources Created |
|---|---|---|
| `main.tf` | Baseline locals, AWS data sources, and provider tag inheritance | — |
| `versions.tf` | Terraform version (`>= 1.9.0, < 2.0.0`) and AWS provider (`~> 6.0`) constraints | — |
| `providers.tf` | Provider configuration with region and default tags | `provider "aws"` |
| `variables.tf` | Validated input variables for compute, network, secrets, alarms | — |
| `networking.tf` | Multi-AZ VPC, public/private subnets, IGW, NAT gateway, VPC endpoints | `aws_vpc`, `aws_subnet`, `aws_internet_gateway`, `aws_nat_gateway`, `aws_route_table`, `aws_vpc_endpoint` |
| `security_groups.tf` | Strict isolation rules for ALB, Frontend, Backend, and VPC endpoints | `aws_security_group` (ALB, Frontend, Backend, Endpoints) |
| `iam.tf` | Least-privilege ECS execution and task roles | `aws_iam_role`, `aws_iam_policy`, `aws_iam_role_policy_attachment` |
| `secrets.tf` | AWS Secrets Manager secret for runtime credentials | `aws_secretsmanager_secret` |
| `alb.tf` | Public ALB, target groups for frontend & backend, path routing rules | `aws_lb`, `aws_lb_target_group`, `aws_lb_listener`, `aws_lb_listener_rule` |
| `ecs.tf` | ECS cluster (Container Insights), task definitions, Fargate services | `aws_ecs_cluster`, `aws_ecs_task_definition`, `aws_ecs_service` |
| `cloudwatch.tf` | Log groups with retention, metric alarms, and SNS alert notifications | `aws_cloudwatch_log_group`, `aws_cloudwatch_metric_alarm`, `aws_sns_topic` |
| `outputs.tf` | Derivable identifiers, ARNs, DNS names, and endpoint references | `output` |

## Resource Inventory (Exact resources defined)

When applied against a target AWS account with staging variables, the following 32 resources are created:

1. `aws_vpc.main`: Dedicated VPC (`10.10.0.0/16`)
2. `aws_subnet.public[0]`: Public Subnet AZ-A
3. `aws_subnet.public[1]`: Public Subnet AZ-B
4. `aws_subnet.private[0]`: Private Subnet AZ-A
5. `aws_subnet.private[1]`: Private Subnet AZ-B
6. `aws_internet_gateway.main`: Internet Gateway
7. `aws_eip.nat[0]`: Elastic IP for NAT Gateway
8. `aws_nat_gateway.main[0]`: NAT Gateway in Public Subnet AZ-A
9. `aws_route_table.public`: Public Route Table (IGW default route)
10. `aws_route_table.private`: Private Route Table (NAT Gateway default route)
11. `aws_route_table_association.public[0..1]`: Associations for public subnets
12. `aws_route_table_association.private[0..1]`: Associations for private subnets
13. `aws_vpc_endpoint.s3[0]`: Gateway endpoint for S3
14. `aws_vpc_endpoint.ecr_api[0]`, `ecr_dkr[0]`, `secretsmanager[0]`, `logs[0]`: Interface VPC endpoints
15. `aws_security_group.alb`: Ingress on 80/443 from allowed CIDRs
16. `aws_security_group.frontend_ecs`: Ingress on port 3000 from ALB SG only
17. `aws_security_group.backend_ecs`: Ingress on port 8000 from ALB and Frontend SGs
18. `aws_security_group.vpc_endpoints`: Ingress on port 443 from ECS SGs
19. `aws_iam_role.ecs_execution`: Execution role for container runtime
20. `aws_iam_role.ecs_task`: Task runtime role
21. `aws_iam_policy.ecs_execution_secrets`: Secrets Manager retrieval policy
22. `aws_secretsmanager_secret.runtime_secrets`: Runtime credentials container
23. `aws_lb.main`: Application Load Balancer in public subnets
24. `aws_lb_target_group.frontend`: Target group for Next.js (port 3000, `/health`)
25. `aws_lb_target_group.backend`: Target group for FastAPI (port 8000, `/health`)
26. `aws_lb_listener.http`: Port 80 listener (default forward to frontend)
27. `aws_lb_listener_rule.backend_direct`: Routing rule for `/backend/*`, `/query`, etc.
28. `aws_ecs_cluster.main`: ECS Cluster with Container Insights
29. `aws_ecs_task_definition.backend`: Backend task definition with secrets injection
30. `aws_ecs_task_definition.frontend`: Frontend task definition
31. `aws_ecs_service.backend` & `aws_ecs_service.frontend`: Private Fargate services
32. `aws_cloudwatch_log_group.backend` & `frontend`: Log retention groups
33. `aws_sns_topic.alerts[0]` & `aws_cloudwatch_metric_alarm.*`: 5 operational alarms

## Remote State Backend Setup (S3 + DynamoDB)

For multi-engineer collaboration and CI/CD automation, state must be stored remotely with locking:

1. **Create S3 bucket and DynamoDB table** (one-time setup per AWS account):
   ```bash
   aws s3api create-bucket --bucket sentinel-tfstate-staging --region us-east-1
   aws s3api put-bucket-versioning --bucket sentinel-tfstate-staging --versioning-configuration Status=Enabled
   aws s3api put-bucket-encryption --bucket sentinel-tfstate-staging --server-side-encryption-configuration '{"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}'

   aws dynamodb create-table \
     --table-name sentinel-tflock-staging \
     --attribute-definitions AttributeName=LockID,AttributeType=S \
     --key-schema AttributeName=LockID,KeyType=HASH \
     --billing-mode PAY_PER_REQUEST \
     --region us-east-1
   ```

2. **Configure backend in `versions.tf`**:
   ```hcl
   terraform {
     backend "s3" {
       bucket         = "sentinel-tfstate-staging"
       key            = "sentinel/staging/terraform.tfstate"
       region         = "us-east-1"
       dynamodb_table = "sentinel-tflock-staging"
       encrypt        = true
     }
   }
   ```

## Development & Verification Workflow

```bash
cd infra/terraform

# 1. Format validation
terraform fmt -check -recursive

# 2. Initialization without cloud backend (hermetic, providers only)
terraform init -backend=false

# 3. Syntax and schema validation
terraform validate

# 4. Plan against staging configuration (requires AWS credentials)
terraform plan -var-file=envs/staging.tfvars.example

# 5. Staging deployment (governed by approval gate)
terraform apply -var-file=envs/staging.tfvars
```
