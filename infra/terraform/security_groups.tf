# Security groups module — network isolation and least-privilege traffic flow.

# -----------------------------------------------------------------------------
# ALB Security Group
# -----------------------------------------------------------------------------

resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb-sg"
  description = "Controls ingress to Sentinel Application Load Balancer"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP ingress from allowed CIDRs and internal VPC"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = distinct(concat(var.alb_ingress_cidr_blocks, [var.vpc_cidr]))
  }

  ingress {
    description = "HTTPS ingress from allowed CIDRs and internal VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = distinct(concat(var.alb_ingress_cidr_blocks, [var.vpc_cidr]))
  }

  egress {
    description = "Outbound to all destinations"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-alb-sg"
  })
}

# -----------------------------------------------------------------------------
# Frontend ECS Security Group
# -----------------------------------------------------------------------------

resource "aws_security_group" "frontend_ecs" {
  name        = "${local.name_prefix}-frontend-ecs-sg"
  description = "Controls traffic to Sentinel Frontend Next.js containers"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Inbound from ALB only"
    from_port       = var.frontend_port
    to_port         = var.frontend_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    description = "Outbound to VPC and external networks"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-frontend-ecs-sg"
  })
}

# -----------------------------------------------------------------------------
# Backend ECS Security Group
# -----------------------------------------------------------------------------

resource "aws_security_group" "backend_ecs" {
  name        = "${local.name_prefix}-backend-ecs-sg"
  description = "Controls traffic to Sentinel Backend FastAPI containers"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Inbound from ALB"
    from_port       = var.backend_port
    to_port         = var.backend_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description     = "Inbound from Frontend containers"
    from_port       = var.backend_port
    to_port         = var.backend_port
    protocol        = "tcp"
    security_groups = [aws_security_group.frontend_ecs.id]
  }

  egress {
    description = "Outbound for external LLM/vector APIs and internal dependencies"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-backend-ecs-sg"
  })
}

# -----------------------------------------------------------------------------
# VPC Endpoints Security Group
# -----------------------------------------------------------------------------

resource "aws_security_group" "vpc_endpoints" {
  name        = "${local.name_prefix}-vpce-sg"
  description = "Controls traffic to VPC Interface Endpoints"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "HTTPS from Frontend ECS"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.frontend_ecs.id]
  }

  ingress {
    description     = "HTTPS from Backend ECS"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.backend_ecs.id]
  }

  egress {
    description = "Outbound to VPC"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-vpce-sg"
  })
}
