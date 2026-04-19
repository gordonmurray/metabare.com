provider "aws" {
  region  = var.region
  profile = var.aws_profile

  default_tags {
    tags = local.common_tags
  }
}

# CloudFront ACM certs must live in us-east-1, regardless of where the
# distribution serves from. Aliased so the rest of the stack can stay
# in eu-west-1.
provider "aws" {
  alias   = "useast1"
  region  = "us-east-1"
  profile = var.aws_profile

  default_tags {
    tags = local.common_tags
  }
}
