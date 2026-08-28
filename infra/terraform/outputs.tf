# Sentinel deployment infrastructure — outputs.

output "name_prefix" {
  description = "Prefix applied to all provisioned resource names."
  value       = local.name_prefix
}

output "environment" {
  description = "Active environment selector."
  value       = var.environment
}

output "vpc_id" {
  description = "ID of the dedicated Sentinel VPC."
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "IDs of the public subnets hosting the ALB."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "IDs of the private subnets hosting ECS tasks."
  value       = aws_subnet.private[*].id
}

output "alb_dns_name" {
  description = "Public DNS name of the Application Load Balancer."
  value       = aws_lb.main.dns_name
}

output "alb_zone_id" {
  description = "Canonical hosted zone ID of the ALB (for Route53 alias records)."
  value       = aws_lb.main.zone_id
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster."
  value       = aws_ecs_cluster.main.name
}

output "backend_service_name" {
  description = "Name of the backend ECS service."
  value       = aws_ecs_service.backend.name
}

output "frontend_service_name" {
  description = "Name of the frontend ECS service."
  value       = aws_ecs_service.frontend.name
}

output "backend_task_definition_arn" {
  description = "ARN of the backend task definition."
  value       = aws_ecs_task_definition.backend.arn
}

output "frontend_task_definition_arn" {
  description = "ARN of the frontend task definition."
  value       = aws_ecs_task_definition.frontend.arn
}

output "runtime_secrets_arn" {
  description = "ARN of the Secrets Manager secret holding runtime credentials."
  value       = aws_secretsmanager_secret.runtime_secrets.arn
}

output "backend_log_group_name" {
  description = "Name of the CloudWatch log group for backend logs."
  value       = aws_cloudwatch_log_group.backend.name
}

output "frontend_log_group_name" {
  description = "Name of the CloudWatch log group for frontend logs."
  value       = aws_cloudwatch_log_group.frontend.name
}

output "alerts_topic_arn" {
  description = "ARN of the SNS topic for operational alarms (null if monitoring disabled)."
  value       = var.enable_monitoring ? aws_sns_topic.alerts[0].arn : null
}
