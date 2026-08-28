# Sentinel deployment configuration — input variables.
#
# Every value a real deployment would need arrives as a variable (or a
# per-environment tfvars file) — nothing deployment-specific is hardcoded in
# resources. Validation keeps invalid configurations out of plan.

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
  description = "Deployment environment. Drives resource naming, remote-state key prefixes, and configuration profile."
  type        = string
  default     = "staging"

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

# -----------------------------------------------------------------------------
# Networking Variables
# -----------------------------------------------------------------------------

variable "vpc_cidr" {
  description = "CIDR block for the dedicated Sentinel VPC."
  type        = string
  default     = "10.0.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "vpc_cidr must be a valid IPv4 CIDR block."
  }
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets (ALB ingress, multi-AZ)."
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]

  validation {
    condition     = length(var.public_subnet_cidrs) >= 2
    error_message = "At least two public subnets across distinct AZs are required for ALB high availability."
  }
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets (ECS tasks, non-routable from internet)."
  type        = list(string)
  default     = ["10.0.10.0/24", "10.0.11.0/24"]

  validation {
    condition     = length(var.private_subnet_cidrs) >= 2
    error_message = "At least two private subnets across distinct AZs are required for ECS placement."
  }
}

variable "enable_vpc_endpoints" {
  description = "Provision cost-efficient private VPC endpoints for ECR, S3, Secrets Manager, and CloudWatch Logs."
  type        = bool
  default     = true
}

variable "enable_nat_gateway" {
  description = "Provision NAT Gateway for outbound internet access from private subnets (needed for external APIs: OpenAI, SEC, News, Pinecone)."
  type        = bool
  default     = true
}

# -----------------------------------------------------------------------------
# Container & Compute Variables
# -----------------------------------------------------------------------------

variable "backend_container_image" {
  description = "Backend container image reference (ECR repo URL or registry tag). Filled from CI."
  type        = string
  default     = ""

  validation {
    condition     = var.backend_container_image == "" || can(regex("^[a-zA-Z0-9][a-zA-Z0-9._/-]+(:[a-zA-Z0-9._-]+)?$", var.backend_container_image))
    error_message = "backend_container_image must be empty or a valid image reference."
  }
}

variable "frontend_container_image" {
  description = "Frontend container image reference (ECR repo URL or registry tag). Filled from CI."
  type        = string
  default     = ""

  validation {
    condition     = var.frontend_container_image == "" || can(regex("^[a-zA-Z0-9][a-zA-Z0-9._/-]+(:[a-zA-Z0-9._-]+)?$", var.frontend_container_image))
    error_message = "frontend_container_image must be empty or a valid image reference."
  }
}

variable "backend_cpu" {
  description = "Fargate CPU units for backend (1024 = 1 vCPU)."
  type        = number
  default     = 512

  validation {
    condition     = var.backend_cpu >= 256 && var.backend_cpu <= 16384 && floor(var.backend_cpu / 256) * 256 == var.backend_cpu
    error_message = "backend_cpu must be between 256 and 16384 in steps of 256."
  }
}

variable "backend_memory_mb" {
  description = "Fargate memory in MiB for backend."
  type        = number
  default     = 1024

  validation {
    condition     = var.backend_memory_mb >= 512 && var.backend_memory_mb <= 122880
    error_message = "backend_memory_mb must be between 512 and 122880."
  }
}

variable "frontend_cpu" {
  description = "Fargate CPU units for frontend (1024 = 1 vCPU)."
  type        = number
  default     = 256

  validation {
    condition     = var.frontend_cpu >= 256 && var.frontend_cpu <= 16384 && floor(var.frontend_cpu / 256) * 256 == var.frontend_cpu
    error_message = "frontend_cpu must be between 256 and 16384 in steps of 256."
  }
}

variable "frontend_memory_mb" {
  description = "Fargate memory in MiB for frontend."
  type        = number
  default     = 512

  validation {
    condition     = var.frontend_memory_mb >= 512 && var.frontend_memory_mb <= 122880
    error_message = "frontend_memory_mb must be between 512 and 122880."
  }
}

variable "backend_desired_count" {
  description = "Number of backend task replicas."
  type        = number
  default     = 1

  validation {
    condition     = var.backend_desired_count >= 1 && var.backend_desired_count <= 10
    error_message = "backend_desired_count must be between 1 and 10."
  }
}

variable "frontend_desired_count" {
  description = "Number of frontend task replicas."
  type        = number
  default     = 1

  validation {
    condition     = var.frontend_desired_count >= 1 && var.frontend_desired_count <= 10
    error_message = "frontend_desired_count must be between 1 and 10."
  }
}

variable "backend_port" {
  description = "Port the backend API listens on."
  type        = number
  default     = 8000
}

variable "frontend_port" {
  description = "Port the frontend Next.js server listens on."
  type        = number
  default     = 3000
}

# -----------------------------------------------------------------------------
# Security & Access Variables
# -----------------------------------------------------------------------------

variable "alb_ingress_cidr_blocks" {
  description = "Allowed CIDR blocks for incoming ALB traffic (restrict to VPN/staging IPs in staging)."
  type        = list(string)
  default     = ["0.0.0.0/0"]

  validation {
    condition     = length(var.alb_ingress_cidr_blocks) > 0 && alltrue([for c in var.alb_ingress_cidr_blocks : can(cidrhost(c, 0))])
    error_message = "Every entry in alb_ingress_cidr_blocks must be a valid IPv4 CIDR block."
  }
}

variable "auth_enabled" {
  description = "Enable single-user API key/Bearer authentication on backend API."
  type        = bool
  default     = true
}

# -----------------------------------------------------------------------------
# External Integrations (Configurable)
# -----------------------------------------------------------------------------

variable "pinecone_index_name" {
  description = "Pinecone vector index name."
  type        = string
  default     = "sentinel"
}

variable "pinecone_cloud" {
  description = "Cloud hosting Pinecone index (aws/gcp/azure)."
  type        = string
  default     = "aws"
}

variable "pinecone_region" {
  description = "Region hosting Pinecone index."
  type        = string
  default     = "us-east-1"
}

variable "enable_langfuse" {
  description = "Enable Langfuse tracing integration."
  type        = bool
  default     = true
}

variable "enable_news_api" {
  description = "Enable News API data source adapter."
  type        = bool
  default     = true
}

# -----------------------------------------------------------------------------
# Logging & Monitoring Variables
# -----------------------------------------------------------------------------

variable "log_retention_days" {
  description = "CloudWatch Logs retention period in days."
  type        = number
  default     = 30

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 180, 365], var.log_retention_days)
    error_message = "log_retention_days must be one of: 1, 3, 5, 7, 14, 30, 60, 90, 180, 365."
  }
}

variable "enable_monitoring" {
  description = "Provision CloudWatch metric alarms and SNS alert topic."
  type        = bool
  default     = true
}

variable "alarm_notification_emails" {
  description = "Email addresses subscribed to CloudWatch operational alarms."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for e in var.alarm_notification_emails : can(regex("^[^@\\s]+@[^@\\s]+\\.[a-z]{2,}$", e))])
    error_message = "Every entry in alarm_notification_emails must be a valid email address."
  }
}

variable "alarm_cpu_threshold_percent" {
  description = "CPU utilization alarm threshold percentage for ECS services."
  type        = number
  default     = 80
}

variable "alarm_memory_threshold_percent" {
  description = "Memory utilization alarm threshold percentage for ECS services."
  type        = number
  default     = 85
}

variable "alarm_5xx_threshold_count" {
  description = "ALB 5XX error count threshold over evaluation period."
  type        = number
  default     = 10
}

variable "alarm_latency_threshold_seconds" {
  description = "ALB target response time threshold in seconds."
  type        = number
  default     = 2.5
}

variable "extra_tags" {
  description = "Additional tags merged onto every provisioned resource."
  type        = map(string)
  default     = {}
}
