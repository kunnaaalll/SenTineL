# Sentinel deployment skeleton — input variables.
#
# Every value a real deployment would need arrives as a variable (or later, a
# per-environment tfvars file) — nothing deployment-specific is hardcoded in
# resources. Validation keeps obvious mistakes out of plan.

variable "project_name" {
  description = "Short project slug used to prefix every resource name."
  type        = string
  default     = "sentinel"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}$", var.project_name))
    error_message = "project_name must be lowercase alphanumeric/hyphens, 2-31 chars, starting with a letter."
  }
}

variable "environment" {
  description = "Deployment environment. Drives resource naming, remote-state key prefixes, and (later) which tfvars/secrets are read."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "aws_region" {
  description = "AWS region for every resource in this stack."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must look like us-east-1 / eu-west-2 / us-gov-west-1."
  }
}

variable "container_image" {
  description = "Backend container image reference (ECR repo URL or registry path). The deployment milestone fills this from the CI-pushed image."
  type        = string
  default     = ""

  validation {
    # Empty is allowed while this is a skeleton; anything set must look like an image ref.
    condition     = var.container_image == "" || can(regex("^[a-zA-Z0-9][a-zA-Z0-9._/-]+(:[a-zA-Z0-9._-]+)?$", var.container_image))
    error_message = "container_image must be empty or a valid image reference like 123456789012.dkr.ecr.us-east-1.amazonaws.com/sentinel-backend:sha-abc123."
  }
}

variable "container_cpu" {
  description = "Fargate task CPU units (1024 = 1 vCPU)."
  type        = number
  default     = 512

  validation {
    condition     = var.container_cpu >= 256 && var.container_cpu <= 16384 && floor(var.container_cpu / 256) * 256 == var.container_cpu
    error_message = "container_cpu must be between 256 and 16384 in steps of 256."
  }
}

variable "container_memory_mb" {
  description = "Fargate task memory in MiB."
  type        = number
  default     = 1024

  validation {
    condition     = var.container_memory_mb >= 512 && var.container_memory_mb <= 122880
    error_message = "container_memory_mb must be between 512 and 122880."
  }
}

variable "desired_count" {
  description = "Number of backend task replicas once ECS hosting lands."
  type        = number
  default     = 1

  validation {
    condition     = var.desired_count >= 1 && var.desired_count <= 10
    error_message = "desired_count must be between 1 and 10 (v1 is single-user scope)."
  }
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for backend logs."
  type        = number
  default     = 30

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 180, 365], var.log_retention_days)
    error_message = "log_retention_days must be one of the CloudWatch allowed values: 1, 3, 5, 7, 14, 30, 60, 90, 180, 365."
  }
}

variable "enable_monitoring" {
  description = "Provision CloudWatch alarms + SNS notifications when the monitoring module lands."
  type        = bool
  default     = true
}

variable "alarm_notification_emails" {
  description = "Addresses subscribed to the alarm SNS topic once the monitoring module lands. Empty until then."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for e in var.alarm_notification_emails : can(regex("^[^@\\s]+@[^@\\s]+\\.[a-z]{2,}$", e))])
    error_message = "Every entry in alarm_notification_emails must be a valid email address."
  }
}

variable "extra_tags" {
  description = "Additional tags merged over the baseline on every resource."
  type        = map(string)
  default     = {}
}
