# The bucket that holds Terraform state for infra/site. It is created once,
# with local state, and kept separate so destroying the site can never touch
# its own state. Destroying this is a deliberate, separate step.

terraform {
  required_version = ">= 1.16"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.67"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "metabare"
      ManagedBy = "terraform"
      Component = "state"
    }
  }
}

variable "region" {
  description = "Region for the state bucket."
  type        = string
  default     = "eu-west-1"
}

resource "aws_s3_bucket" "state" {
  bucket_prefix = "metabare-tfstate-"
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Old state versions are kept for 90 days, enough to recover from a bad apply.
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

output "state_bucket" {
  description = "Put this in infra/site/backend.hcl."
  value       = aws_s3_bucket.state.bucket
}
