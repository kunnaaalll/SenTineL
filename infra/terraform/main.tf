# Sentinel deployment skeleton — AWS target (spec section 14.2).
#
# STATUS: SKELETON. This file intentionally defines ZERO resources. It has
# never been applied; `terraform apply` here is a no-op by construction.
# The blocks below are the extension points the deployment milestone fills
# in, one module at a time, with the input/output contracts noted.

locals {
  name_prefix = "${var.project_name}-${var.environment}"

  # Baseline tag set — also consumed by providers.tf default_tags, so even
  # provider-managed tagging stays consistent once resources exist.
  tags = merge(
    {
      Project     = var.project_name,
      Environment = var.environment,
      ManagedBy   = "terraform",
      Component   = "sentinel-backend",
    },
    var.extra_tags,
  )
}

# -----------------------------------------------------------------------------
# EXTENSION POINTS (planned module boundaries — uncomment/implement in order)
#
# Each future module takes locals.name_prefix + local.tags and exports the
# attributes listed, so wiring stays declarative and reviewable.
# -----------------------------------------------------------------------------

# 1) Networking ---------------------------------------------------------------
# Private-subnet VPC with NAT or — preferred for cost — interface/gateway
# VPC endpoints for ECR (api + dkr), S3, Secrets Manager, CloudWatch Logs.
# Exports: vpc_id, private_subnet_ids.
#
# module "network" {
#   source             = "./modules/network"
#   name_prefix        = local.name_prefix
#   tags               = local.tags
# }

# 2) Secrets management -------------------------------------------------------
# One AWS Secrets Manager secret per environment holding the runtime env vars
# (OPENAI_API_KEY, PINECONE_API_KEY, NEWS_API_KEY, LANGFUSE_*, SEC_CONTACT_EMAIL).
# The ECS task execution role reads it at task start — values never live in
# tfvars, task definitions, or git.
# Exports: secret_arn, secret_name.
#
# module "secrets" {
#   source       = "./modules/secrets"
#   name_prefix  = local.name_prefix
#   environment  = var.environment
#   tags         = local.tags
# }

# 3) IAM ----------------------------------------------------------------------
# Task execution role: ECR pull, CloudWatch Logs, secretsmanager:GetSecretValue
# scoped to module.secrets.secret_arn only. Task role: minimum viable — the
# backend currently talks only to third-party APIs over egress.
# Exports: execution_role_arn, task_role_arn.
#
# module "iam" {
#   source      = "./modules/iam"
#   name_prefix = local.name_prefix
#   tags        = local.tags
# }

# 4) Container hosting --------------------------------------------------------
# ECS Fargate cluster + service running var.container_image with
# cpu/memory from variables, one task in a private subnet, no public IP,
# behind an ALB restricted to the intended network for staging/prod.
# NOTE: v1 has NO application authentication (spec section 17) — the service
# must not be internet-exposed until auth exists. WAF/rule work belongs here.
# Exports: cluster_name, service_name, task_definition_arn.
#
# module "hosting" {
#   source           = "./modules/hosting"
#   name_prefix      = local.name_prefix
#   image            = var.container_image
#   cpu              = var.container_cpu
#   memory_mb        = var.container_memory_mb
#   desired_count    = var.desired_count
#   subnet_ids       = module.network.private_subnet_ids
#   execution_role   = module.iam.execution_role_arn
#   task_role        = module.iam.task_role_arn
#   secret_arn       = module.secrets.secret_arn
#   tags             = local.tags
# }

# 5) Logging ------------------------------------------------------------------
# CloudWatch log group /sentinel/${local.name_prefix}/backend with
# retention var.log_retention_days; the task definition points here.
#
# module "logging" {
#   source          = "./modules/logging"
#   name_prefix     = local.name_prefix
#   retention_days  = var.log_retention_days
#   tags            = local.tags
# }

# 6) Monitoring ---------------------------------------------------------------
# Alarms on CPU/memory/task-count plus API 5xx rate; SNS topic for delivery;
# gated on var.enable_monitoring.
#
# module "monitoring" {
#   count              = var.enable_monitoring ? 1 : 0
#   source             = "./modules/monitoring"
#   name_prefix        = local.name_prefix
#   service_name       = module.hosting.service_name
#   notification_emails = var.alarm_notification_emails
#   tags               = local.tags
# }
