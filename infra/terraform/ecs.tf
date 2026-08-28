# ECS Fargate module — Cluster, task definitions, and container services.

resource "aws_ecs_cluster" "main" {
  name = "${local.name_prefix}-ecs-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-ecs-cluster"
  })
}

# -----------------------------------------------------------------------------
# Backend Task Definition & Service
# -----------------------------------------------------------------------------

resource "aws_ecs_task_definition" "backend" {
  family                   = "${local.name_prefix}-backend"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.backend_cpu)
  memory                   = tostring(var.backend_memory_mb)
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = "backend"
      image     = var.backend_container_image != "" ? var.backend_container_image : "${local.name_prefix}-backend:staging"
      essential = true
      portMappings = [
        {
          containerPort = var.backend_port
          hostPort      = var.backend_port
          protocol      = "tcp"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.backend.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "backend"
        }
      }
      environment = [
        { name = "SENTINEL_ENV", value = var.environment },
        { name = "PINECONE_INDEX_NAME", value = var.pinecone_index_name },
        { name = "PINECONE_CLOUD", value = var.pinecone_cloud },
        { name = "PINECONE_REGION", value = var.pinecone_region },
        { name = "AUTH_ENABLED", value = tostring(var.auth_enabled) },
        { name = "LOG_FORMAT", value = "json" },
        { name = "LOG_LEVEL", value = "INFO" }
      ]
      secrets = [
        {
          name      = "OPENAI_API_KEY"
          valueFrom = "${aws_secretsmanager_secret.runtime_secrets.arn}:OPENAI_API_KEY::"
        },
        {
          name      = "PINECONE_API_KEY"
          valueFrom = "${aws_secretsmanager_secret.runtime_secrets.arn}:PINECONE_API_KEY::"
        },
        {
          name      = "NEWS_API_KEY"
          valueFrom = "${aws_secretsmanager_secret.runtime_secrets.arn}:NEWS_API_KEY::"
        },
        {
          name      = "LANGFUSE_PUBLIC_KEY"
          valueFrom = "${aws_secretsmanager_secret.runtime_secrets.arn}:LANGFUSE_PUBLIC_KEY::"
        },
        {
          name      = "LANGFUSE_SECRET_KEY"
          valueFrom = "${aws_secretsmanager_secret.runtime_secrets.arn}:LANGFUSE_SECRET_KEY::"
        },
        {
          name      = "AUTH_API_KEY"
          valueFrom = "${aws_secretsmanager_secret.runtime_secrets.arn}:AUTH_API_KEY::"
        },
        {
          name      = "SEC_CONTACT_EMAIL"
          valueFrom = "${aws_secretsmanager_secret.runtime_secrets.arn}:SEC_CONTACT_EMAIL::"
        }
      ]
    }
  ])

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-backend-task"
  })
}

resource "aws_ecs_service" "backend" {
  name                   = "${local.name_prefix}-backend-svc"
  cluster                = aws_ecs_cluster.main.id
  task_definition        = aws_ecs_task_definition.backend.arn
  desired_count          = var.backend_desired_count
  launch_type            = "FARGATE"
  enable_execute_command = false

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.backend_ecs.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.backend.arn
    container_name   = "backend"
    container_port   = var.backend_port
  }

  depends_on = [aws_lb_listener.http]

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-backend-svc"
  })
}

# -----------------------------------------------------------------------------
# Frontend Task Definition & Service
# -----------------------------------------------------------------------------

resource "aws_ecs_task_definition" "frontend" {
  family                   = "${local.name_prefix}-frontend"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.frontend_cpu)
  memory                   = tostring(var.frontend_memory_mb)
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = "frontend"
      image     = var.frontend_container_image != "" ? var.frontend_container_image : "${local.name_prefix}-frontend:staging"
      essential = true
      portMappings = [
        {
          containerPort = var.frontend_port
          hostPort      = var.frontend_port
          protocol      = "tcp"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.frontend.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "frontend"
        }
      }
      environment = [
        { name = "NODE_ENV", value = "production" },
        { name = "BACKEND_ORIGIN", value = "http://${aws_lb.main.dns_name}" }
      ]
    }
  ])

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-frontend-task"
  })
}

resource "aws_ecs_service" "frontend" {
  name                   = "${local.name_prefix}-frontend-svc"
  cluster                = aws_ecs_cluster.main.id
  task_definition        = aws_ecs_task_definition.frontend.arn
  desired_count          = var.frontend_desired_count
  launch_type            = "FARGATE"
  enable_execute_command = false

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.frontend_ecs.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.frontend.arn
    container_name   = "frontend"
    container_port   = var.frontend_port
  }

  depends_on = [aws_lb_listener.http]

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-frontend-svc"
  })
}
