# Sentinel deployment skeleton — Terraform version + provider pins.
#
# STATUS: SKELETON. main.tf defines no resources yet; nothing in this
# directory has ever been applied against any AWS account.

terraform {
  # Pin the minor line: language/features stay predictable across the team.
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # --------------------------------------------------------------------------
  # EXAMPLE ONLY — remote state backend. NOT enabled; do not uncomment until
  # the bootstrap in README.md ("Remote state") has been run once manually.
  # Replace EXAMPLE-* placeholders; never commit a filled-in copy with real
  # account IDs or bucket names.
  #
  # Per-environment isolation comes from distinct key prefixes (see README).
  # Backend blocks accept only literal values — ${var.environment} and other
  # interpolations are forbidden there — so edit the key by hand per
  # environment, e.g. key = "sentinel/staging/terraform.tfstate".
  #
  # backend "s3" {
  #   bucket         = "EXAMPLE-sentinel-tfstate-ACCOUNTID"
  #   key            = "sentinel/dev/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "EXAMPLE-sentinel-tflock"
  #   encrypt        = true
  # }
  # --------------------------------------------------------------------------
}
