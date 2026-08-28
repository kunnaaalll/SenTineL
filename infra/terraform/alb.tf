# Application Load Balancer (ALB) module — Ingress routing and health checks.

resource "aws_lb" "main" {
  name               = "${local.name_prefix}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  enable_deletion_protection = false # Staging lifecycle

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-alb"
  })
}

# -----------------------------------------------------------------------------
# Target Groups
# -----------------------------------------------------------------------------

resource "aws_lb_target_group" "frontend" {
  name                 = "${local.name_prefix}-tg-frontend"
  port                 = var.frontend_port
  protocol             = "HTTP"
  vpc_id               = aws_vpc.main.id
  target_type          = "ip"
  deregistration_delay = 30

  health_check {
    enabled             = true
    path                = "/health"
    port                = "traffic-port"
    protocol            = "HTTP"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-tg-frontend"
  })
}

resource "aws_lb_target_group" "backend" {
  name                 = "${local.name_prefix}-tg-backend"
  port                 = var.backend_port
  protocol             = "HTTP"
  vpc_id               = aws_vpc.main.id
  target_type          = "ip"
  deregistration_delay = 30

  health_check {
    enabled             = true
    path                = "/health"
    port                = "traffic-port"
    protocol            = "HTTP"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-tg-backend"
  })
}

# -----------------------------------------------------------------------------
# Listeners and Routing Rules
# -----------------------------------------------------------------------------

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  # Default: route to the Next.js Frontend
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.frontend.arn
  }

  tags = local.tags
}

resource "aws_lb_listener_rule" "backend_direct" {
  listener_arn = local.listener_rule_target != null ? aws_lb.main.arn : aws_lb_listener.http.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.backend.arn
  }

  condition {
    path_pattern {
      values = [
        "/query",
        "/agents/*",
        "/ingest",
        "/sources",
        "/providers",
        "/ready",
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json"
      ]
    }
  }

  tags = local.tags
}

locals {
  # Helper placeholder for optional SSL listener extension
  listener_rule_target = null
}
