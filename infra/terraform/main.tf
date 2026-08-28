# Sentinel staging & production infrastructure — AWS target.
#
# Module / resource map:
# - networking.tf: Dedicated VPC, public & private subnets, IGW, NAT Gateway, route tables, VPC endpoints.
# - security_groups.tf: Strict ingress/egress rules for ALB, Frontend, Backend, and VPC endpoints.
# - iam.tf: ECS task execution role (ECR, Logs, Secrets Manager) and least-privilege task role.
# - secrets.tf: AWS Secrets Manager reference for runtime environment credentials.
# - alb.tf: Internet-facing Application Load Balancer with frontend & backend target groups and routing rules.
# - ecs.tf: ECS Fargate cluster, task definitions, and services for backend and frontend.
# - cloudwatch.tf: Log groups, metric alarms (CPU, Memory, 5xx, Latency, Unhealthy tasks), and SNS alerts.

locals {
  name_prefix = "${var.project_name}-${var.environment}"

  # Baseline tag set applied by default_tags and merged across resources
  tags = merge(
    {
      Project     = var.project_name,
      Environment = var.environment,
      ManagedBy   = "terraform",
      Component   = "sentinel-stack",
    },
    var.extra_tags,
  )
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_partition" "current" {}

data "aws_caller_identity" "current" {}
