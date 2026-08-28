# Sentinel Staging Bootstrap Infrastructure Stack
#
# PURPOSE:
# This bootstrap stack provisions the foundational AWS resources required
# BEFORE the main application Terraform stack can be initialized or applied:
# 1. S3 bucket for main stack Terraform remote state (versioned, encrypted, private)
# 2. DynamoDB table for distributed Terraform state locking
# 3. ECR repositories for container image storage (immutable tags, scan on push)
# 4. IAM OIDC Provider and least-privilege deployment role for GitHub Actions
#
# IMPORTANT:
# - Apply this stack ONCE per AWS account/environment using an administrative role
#   (e.g., AWS SSO / IAM Identity Center admin role).
# - NEVER run this stack or the main stack using AWS Root account credentials.
# - DO NOT run `terraform apply` during preflight or audit without explicit approval.

terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "sentinel-bootstrap"
      Component   = "bootstrap"
    }
  }
}

# -----------------------------------------------------------------------------
# Input Variables
# -----------------------------------------------------------------------------

variable "project_name" {
  description = "Project name slug."
  type        = string
  default     = "sentinel"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}$", var.project_name))
    error_message = "project_name must be lowercase alphanumeric/hyphens, 2-31 chars."
  }
}

variable "environment" {
  description = "Deployment environment (e.g. staging, prod)."
  type        = string
  default     = "staging"

  validation {
    condition     = contains(["staging", "prod"], var.environment)
    error_message = "environment must be staging or prod."
  }
}

variable "aws_region" {
  description = "AWS region for bootstrap resources."
  type        = string
  default     = "us-east-1"
}

variable "github_repository" {
  description = "GitHub repository in format owner/repo (e.g. kunalparmar/sentinel)."
  type        = string
  default     = "kunalparmar/sentinel"

  validation {
    condition     = can(regex("^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must look like owner/repo."
  }
}

variable "create_oidc_provider" {
  description = "Whether to create the GitHub OIDC provider (false if it already exists in the AWS account)."
  type        = bool
  default     = true
}

# -----------------------------------------------------------------------------
# Data Sources
# -----------------------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  name_prefix = "${var.project_name}-${var.environment}"
  account_id  = data.aws_caller_identity.current.account_id
}

# -----------------------------------------------------------------------------
# 1. S3 Bucket for Terraform Remote State
# -----------------------------------------------------------------------------

resource "aws_s3_bucket" "tfstate" {
  bucket = "${local.name_prefix}-tfstate-${local.account_id}"

  # Protect state from accidental deletion in production
  force_destroy = var.environment == "staging" ? false : false

  lifecycle {
    prevent_destroy = false
  }

  tags = {
    Name    = "${local.name_prefix}-tfstate"
    Purpose = "Terraform remote state storage"
  }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "tfstate" {
  bucket     = aws_s3_bucket.tfstate.id
  depends_on = [aws_s3_bucket_versioning.tfstate]

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "cleanup-old-versions"
    status = "Enabled"

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

# -----------------------------------------------------------------------------
# 2. DynamoDB Table for Terraform State Locking
# -----------------------------------------------------------------------------

resource "aws_dynamodb_table" "tflock" {
  name         = "${local.name_prefix}-tflock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name    = "${local.name_prefix}-tflock"
    Purpose = "Terraform state distributed locking"
  }
}

# -----------------------------------------------------------------------------
# 3. ECR Repositories for Container Images
# -----------------------------------------------------------------------------

resource "aws_ecr_repository" "backend" {
  name                 = "${var.project_name}-backend"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = {
    Name    = "${var.project_name}-backend"
    Purpose = "FastAPI backend container images"
  }
}

resource "aws_ecr_repository" "frontend" {
  name                 = "${var.project_name}-frontend"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = {
    Name    = "${var.project_name}-frontend"
    Purpose = "Next.js frontend container images"
  }
}

# ECR Lifecycle Policy (retain last 30 release images, expire untagged after 7 days)
resource "aws_ecr_lifecycle_policy" "backend" {
  repository = aws_ecr_repository.backend.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images older than 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 2
        description  = "Retain latest 30 tagged release images"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["git-", "v"]
          countType     = "imageCountMoreThan"
          countNumber   = 30
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

resource "aws_ecr_lifecycle_policy" "frontend" {
  repository = aws_ecr_repository.frontend.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images older than 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 2
        description  = "Retain latest 30 tagged release images"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["git-", "v"]
          countType     = "imageCountMoreThan"
          countNumber   = 30
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# 4. GitHub Actions OIDC Provider & Deployment IAM Role
# -----------------------------------------------------------------------------

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 1 : 0

  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["ffffffffffffffffffffffffffffffffffffffff", "6938fd4d98bab03faadb97b34396831e3780aea1"]

  tags = {
    Name = "github-actions-oidc-provider"
  }
}

locals {
  oidc_provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : "arn:${data.aws_partition.current.partition}:iam::${local.account_id}:oidc-provider/token.actions.githubusercontent.com"
}

resource "aws_iam_role" "github_actions_staging" {
  name        = "${local.name_prefix}-github-actions"
  description = "Role assumed by GitHub Actions for Sentinel ${var.environment} deployments"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = local.oidc_provider_arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          }
          StringLike = {
            # Tightly bound to repository and staging environment or main branch
            "token.actions.githubusercontent.com:sub" = [
              "repo:${var.github_repository}:environment:${var.environment}",
              "repo:${var.github_repository}:ref:refs/heads/main"
            ]
          }
        }
      }
    ]
  })

  tags = {
    Name    = "${local.name_prefix}-github-actions"
    Purpose = "GitHub Actions OIDC deployment role"
  }
}

# Granular IAM Policy for Staging Infrastructure Management
resource "aws_iam_policy" "staging_deployment_policy" {
  name        = "${local.name_prefix}-deployment-policy"
  description = "Least-privilege policy for Sentinel ${var.environment} infrastructure management"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Remote state access
      {
        Sid    = "TerraformStateAccess"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.tfstate.arn,
          "${aws_s3_bucket.tfstate.arn}/*"
        ]
      },
      # State lock access
      {
        Sid    = "TerraformLockAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:DeleteItem",
          "dynamodb:DescribeTable"
        ]
        Resource = aws_dynamodb_table.tflock.arn
      },
      # Networking permissions
      {
        Sid    = "NetworkingManagement"
        Effect = "Allow"
        Action = [
          "ec2:CreateVpc",
          "ec2:DeleteVpc",
          "ec2:DescribeVpcs",
          "ec2:ModifyVpcAttribute",
          "ec2:CreateSubnet",
          "ec2:DeleteSubnet",
          "ec2:DescribeSubnets",
          "ec2:ModifySubnetAttribute",
          "ec2:CreateInternetGateway",
          "ec2:DeleteInternetGateway",
          "ec2:AttachInternetGateway",
          "ec2:DetachInternetGateway",
          "ec2:DescribeInternetGateways",
          "ec2:CreateRouteTable",
          "ec2:DeleteRouteTable",
          "ec2:DescribeRouteTables",
          "ec2:CreateRoute",
          "ec2:DeleteRoute",
          "ec2:AssociateRouteTable",
          "ec2:DisassociateRouteTable",
          "ec2:AllocateAddress",
          "ec2:ReleaseAddress",
          "ec2:DescribeAddresses",
          "ec2:CreateNatGateway",
          "ec2:DeleteNatGateway",
          "ec2:DescribeNatGateways",
          "ec2:CreateSecurityGroup",
          "ec2:DeleteSecurityGroup",
          "ec2:DescribeSecurityGroups",
          "ec2:AuthorizeSecurityGroupIngress",
          "ec2:RevokeSecurityGroupIngress",
          "ec2:AuthorizeSecurityGroupEgress",
          "ec2:RevokeSecurityGroupEgress",
          "ec2:CreateVpcEndpoint",
          "ec2:DeleteVpcEndpoints",
          "ec2:DescribeVpcEndpoints",
          "ec2:DescribeAvailabilityZones",
          "ec2:DescribeVpcEndpointServices",
          "ec2:CreateTags",
          "ec2:DeleteTags",
          "ec2:DescribeTags"
        ]
        Resource = "*"
      },
      # Application Load Balancer permissions
      {
        Sid    = "ALBManagement"
        Effect = "Allow"
        Action = [
          "elasticloadbalancing:CreateLoadBalancer",
          "elasticloadbalancing:DeleteLoadBalancer",
          "elasticloadbalancing:DescribeLoadBalancers",
          "elasticloadbalancing:DescribeLoadBalancerAttributes",
          "elasticloadbalancing:ModifyLoadBalancerAttributes",
          "elasticloadbalancing:CreateTargetGroup",
          "elasticloadbalancing:DeleteTargetGroup",
          "elasticloadbalancing:DescribeTargetGroups",
          "elasticloadbalancing:DescribeTargetGroupAttributes",
          "elasticloadbalancing:ModifyTargetGroupAttributes",
          "elasticloadbalancing:CreateListener",
          "elasticloadbalancing:DeleteListener",
          "elasticloadbalancing:DescribeListeners",
          "elasticloadbalancing:CreateRule",
          "elasticloadbalancing:DeleteRule",
          "elasticloadbalancing:DescribeRules",
          "elasticloadbalancing:ModifyRule",
          "elasticloadbalancing:AddTags",
          "elasticloadbalancing:RemoveTags",
          "elasticloadbalancing:DescribeTags"
        ]
        Resource = "*"
      },
      # ECS Cluster, Services, and Task Definitions
      {
        Sid    = "ECSManagement"
        Effect = "Allow"
        Action = [
          "ecs:CreateCluster",
          "ecs:DeleteCluster",
          "ecs:DescribeClusters",
          "ecs:UpdateCluster",
          "ecs:UpdateClusterSettings",
          "ecs:CreateService",
          "ecs:DeleteService",
          "ecs:DescribeServices",
          "ecs:UpdateService",
          "ecs:RegisterTaskDefinition",
          "ecs:DeregisterTaskDefinition",
          "ecs:DescribeTaskDefinition",
          "ecs:TagResource",
          "ecs:UntagResource",
          "ecs:ListTagsForResource"
        ]
        Resource = "*"
      },
      # CloudWatch Logs & Alarms
      {
        Sid    = "CloudWatchManagement"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:DeleteLogGroup",
          "logs:DescribeLogGroups",
          "logs:PutRetentionPolicy",
          "logs:DeleteRetentionPolicy",
          "logs:ListTagsLogGroup",
          "logs:TagLogGroup",
          "logs:UntagLogGroup",
          "cloudwatch:PutMetricAlarm",
          "cloudwatch:DeleteAlarms",
          "cloudwatch:DescribeAlarms",
          "cloudwatch:ListTagsForResource",
          "cloudwatch:TagResource",
          "cloudwatch:UntagResource"
        ]
        Resource = "*"
      },
      # SNS Topics for Alarms
      {
        Sid    = "SNSManagement"
        Effect = "Allow"
        Action = [
          "sns:CreateTopic",
          "sns:DeleteTopic",
          "sns:GetTopicAttributes",
          "sns:SetTopicAttributes",
          "sns:Subscribe",
          "sns:Unsubscribe",
          "sns:ListSubscriptionsByTopic",
          "sns:TagResource",
          "sns:UntagResource"
        ]
        Resource = "arn:${data.aws_partition.current.partition}:sns:${var.aws_region}:${local.account_id}:${local.name_prefix}-*"
      },
      # Secrets Manager Secret Resource Management (metadata only, no read of plaintext values)
      {
        Sid    = "SecretsManagerManagement"
        Effect = "Allow"
        Action = [
          "secretsmanager:CreateSecret",
          "secretsmanager:DeleteSecret",
          "secretsmanager:DescribeSecret",
          "secretsmanager:TagResource",
          "secretsmanager:UntagResource",
          "secretsmanager:GetResourcePolicy"
        ]
        Resource = "arn:${data.aws_partition.current.partition}:secretsmanager:${var.aws_region}:${local.account_id}:secret:${local.name_prefix}-*"
      },
      # IAM Roles & Policies for ECS (PassRole and scoped management)
      {
        Sid    = "IAMRolesManagement"
        Effect = "Allow"
        Action = [
          "iam:CreateRole",
          "iam:DeleteRole",
          "iam:GetRole",
          "iam:GetRolePolicy",
          "iam:PassRole",
          "iam:AttachRolePolicy",
          "iam:DetachRolePolicy",
          "iam:PutRolePolicy",
          "iam:DeleteRolePolicy",
          "iam:CreatePolicy",
          "iam:DeletePolicy",
          "iam:GetPolicy",
          "iam:GetPolicyVersion",
          "iam:ListPolicyVersions",
          "iam:ListAttachedRolePolicies",
          "iam:TagRole",
          "iam:UntagRole",
          "iam:TagPolicy",
          "iam:UntagPolicy"
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:iam::${local.account_id}:role/${local.name_prefix}-*",
          "arn:${data.aws_partition.current.partition}:iam::${local.account_id}:policy/${local.name_prefix}-*"
        ]
      },
      # ECR Image Push & Pull permissions for CI workflow
      {
        Sid    = "ECRAccess"
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:PutImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:DescribeRepositories"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "staging_deployment" {
  role       = aws_iam_role.github_actions_staging.name
  policy_arn = aws_iam_policy.staging_deployment_policy.arn
}

# -----------------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------------

output "s3_tfstate_bucket" {
  description = "Name of the S3 bucket for Terraform remote state."
  value       = aws_s3_bucket.tfstate.bucket
}

output "dynamodb_tflock_table" {
  description = "Name of the DynamoDB table for Terraform state locking."
  value       = aws_dynamodb_table.tflock.name
}

output "ecr_backend_repository_url" {
  description = "URL of the backend ECR repository."
  value       = aws_ecr_repository.backend.repository_url
}

output "ecr_frontend_repository_url" {
  description = "URL of the frontend ECR repository."
  value       = aws_ecr_repository.frontend.repository_url
}

output "github_actions_role_arn" {
  description = "ARN of the IAM role for GitHub Actions OIDC assumption."
  value       = aws_iam_role.github_actions_staging.arn
}

output "backend_config_snippet" {
  description = "Snippet to configure remote backend in infra/terraform/versions.tf or via -backend-config."
  value       = <<-EOT
    bucket         = "${aws_s3_bucket.tfstate.bucket}"
    key            = "${var.project_name}/${var.environment}/terraform.tfstate"
    region         = "${var.aws_region}"
    dynamodb_table = "${aws_dynamodb_table.tflock.name}"
    encrypt        = true
  EOT
}
