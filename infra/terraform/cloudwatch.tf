# CloudWatch module — Log groups, operational alarms, and SNS notifications.

# -----------------------------------------------------------------------------
# Log Groups
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "backend" {
  name              = "/${var.project_name}/${var.environment}/backend"
  retention_in_days = var.log_retention_days

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-backend-logs"
  })
}

resource "aws_cloudwatch_log_group" "frontend" {
  name              = "/${var.project_name}/${var.environment}/frontend"
  retention_in_days = var.log_retention_days

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-frontend-logs"
  })
}

# -----------------------------------------------------------------------------
# SNS Alerting Topic
# -----------------------------------------------------------------------------

resource "aws_sns_topic" "alerts" {
  count = var.enable_monitoring ? 1 : 0
  name  = "${local.name_prefix}-operational-alerts"

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-alerts-topic"
  })
}

resource "aws_sns_topic_subscription" "email_subscribers" {
  count     = var.enable_monitoring ? length(var.alarm_notification_emails) : 0
  topic_arn = aws_sns_topic.alerts[0].arn
  protocol  = "email"
  endpoint  = var.alarm_notification_emails[count.index]
}

# -----------------------------------------------------------------------------
# CloudWatch Metric Alarms
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "backend_cpu_high" {
  count               = var.enable_monitoring ? 1 : 0
  alarm_name          = "${local.name_prefix}-backend-cpu-high"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = 60
  statistic           = "Average"
  threshold           = var.alarm_cpu_threshold_percent
  alarm_description   = "Sentinel backend ECS service CPU utilization exceeds ${var.alarm_cpu_threshold_percent}%"
  alarm_actions       = [aws_sns_topic.alerts[0].arn]
  ok_actions          = [aws_sns_topic.alerts[0].arn]

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.backend.name
  }

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "backend_memory_high" {
  count               = var.enable_monitoring ? 1 : 0
  alarm_name          = "${local.name_prefix}-backend-memory-high"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 2
  metric_name         = "MemoryUtilization"
  namespace           = "AWS/ECS"
  period              = 60
  statistic           = "Average"
  threshold           = var.alarm_memory_threshold_percent
  alarm_description   = "Sentinel backend ECS service memory utilization exceeds ${var.alarm_memory_threshold_percent}%"
  alarm_actions       = [aws_sns_topic.alerts[0].arn]
  ok_actions          = [aws_sns_topic.alerts[0].arn]

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.backend.name
  }

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx_errors" {
  count               = var.enable_monitoring ? 1 : 0
  alarm_name          = "${local.name_prefix}-alb-high-5xx"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  threshold           = var.alarm_5xx_threshold_count
  alarm_description   = "Sentinel ALB backend target group 5XX errors exceed ${var.alarm_5xx_threshold_count} in 1 minute"
  alarm_actions       = [aws_sns_topic.alerts[0].arn]

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
    TargetGroup  = aws_lb_target_group.backend.arn_suffix
  }

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "target_response_time" {
  count               = var.enable_monitoring ? 1 : 0
  alarm_name          = "${local.name_prefix}-alb-target-latency-high"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 2
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  extended_statistic  = "p95"
  threshold           = var.alarm_latency_threshold_seconds
  alarm_description   = "Sentinel ALB target p95 latency exceeds ${var.alarm_latency_threshold_seconds}s"
  alarm_actions       = [aws_sns_topic.alerts[0].arn]

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
    TargetGroup  = aws_lb_target_group.backend.arn_suffix
  }

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "backend_unhealthy_hosts" {
  count               = var.enable_monitoring ? 1 : 0
  alarm_name          = "${local.name_prefix}-backend-unhealthy-hosts"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "One or more Sentinel backend container instances failed health checks"
  alarm_actions       = [aws_sns_topic.alerts[0].arn]

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
    TargetGroup  = aws_lb_target_group.backend.arn_suffix
  }

  tags = local.tags
}
