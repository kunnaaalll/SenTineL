# IAM module — Least privilege roles for ECS task execution and container runtime.

# -----------------------------------------------------------------------------
# ECS Task Execution Role (Pulls images, fetches secrets, pushes logs)
# -----------------------------------------------------------------------------

resource "aws_iam_role" "ecs_execution" {
  name        = "${local.name_prefix}-ecs-execution-role"
  description = "Execution role for Sentinel ECS tasks to pull images and resolve secrets"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "ecs_execution_standard" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_policy" "ecs_execution_secrets" {
  name        = "${local.name_prefix}-secrets-access-policy"
  description = "Grants ECS execution role access to the Sentinel runtime secret"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret"
        ]
        Resource = [
          aws_secretsmanager_secret.runtime_secrets.arn,
          "${aws_secretsmanager_secret.runtime_secrets.arn}:*"
        ]
      }
    ]
  })

  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "ecs_execution_secrets" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = aws_iam_policy.ecs_execution_secrets.arn
}

# -----------------------------------------------------------------------------
# ECS Task Role (Runtime permissions for application containers)
# -----------------------------------------------------------------------------

resource "aws_iam_role" "ecs_task" {
  name        = "${local.name_prefix}-ecs-task-role"
  description = "Runtime role assumed by Sentinel application containers"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  tags = local.tags
}
