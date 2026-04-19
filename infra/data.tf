data "aws_caller_identity" "current" {}

# Latest Amazon Linux 2023 AMI for x86_64.
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }

  filter {
    name   = "root-device-type"
    values = ["ebs"]
  }
}

data "aws_cloudfront_cache_policy" "managed_caching_optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_origin_request_policy" "managed_cors_s3_origin" {
  name = "Managed-CORS-S3Origin"
}

locals {
  common_tags = {
    Project   = "metabare"
    Env       = var.env
    Owner     = "gordonmurray"
    ManagedBy = "terraform"
  }

  data_bucket_name = "metabare-${var.env}-${data.aws_caller_identity.current.account_id}"
}
