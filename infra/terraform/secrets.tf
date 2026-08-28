# Secrets management module — AWS Secrets Manager configuration.
#
# Credentials (OPENAI_API_KEY, PINECONE_API_KEY, NEWS_API_KEY, LANGFUSE_*,
# AUTH_API_KEY, SEC_CONTACT_EMAIL) live in AWS Secrets Manager and are read
# by the ECS task execution role at task startup.
# Values NEVER live in tfvars, task definitions, or git.

resource "aws_secretsmanager_secret" "runtime_secrets" {
  name                    = "${local.name_prefix}-runtime-secrets"
  description             = "Runtime environment secrets for Sentinel ${var.environment}"
  recovery_window_in_days = var.environment == "prod" ? 30 : 0 # Immediate deletion for dev/staging; 30-day protection for prod

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-runtime-secrets"
  })
}

# Documentation / Schema template for the runtime secret JSON payload:
# {
#   "OPENAI_API_KEY": "sk-...",
#   "PINECONE_API_KEY": "pcsk_...",
#   "NEWS_API_KEY": "...",
#   "LANGFUSE_PUBLIC_KEY": "pk-lf-...",
#   "LANGFUSE_SECRET_KEY": "sk-lf-...",
#   "AUTH_API_KEY": "sentinel-staging-key-...",
#   "SEC_CONTACT_EMAIL": "operator@domain.com"
# }
