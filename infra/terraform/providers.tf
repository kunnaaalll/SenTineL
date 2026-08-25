# Sentinel deployment skeleton — AWS provider configuration.
#
# Credentials are NEVER declared here. Authenticate out-of-band, e.g.:
#     aws sso login --profile sentinel-dev
#     export AWS_PROFILE=sentinel-dev
# Terraform picks them up from the standard credential chain.

provider "aws" {
  region = var.aws_region

  # Every resource this stack will ever create carries the same baseline
  # tags — cost allocation and environment filtering stay consistent.
  default_tags {
    tags = local.tags
  }
}
