variable "aws_profile" {
  type        = string
  default     = "cloudfloe"
  description = "Local AWS CLI profile used for the apply"
}

variable "region" {
  type        = string
  default     = "eu-west-1"
  description = "Primary region for everything except the CloudFront ACM cert"
}

variable "env" {
  type    = string
  default = "prod"
}

variable "domain" {
  type        = string
  default     = "metabare.com"
  description = "Apex hostname served by the ALB"
}

variable "cdn_domain" {
  type        = string
  default     = "cdn.metabare.com"
  description = "Hostname served by CloudFront for image delivery"
}

variable "instance_type" {
  type        = string
  default     = "m7i.xlarge"
  description = "Fixed-performance general-purpose instance. Baseline t3.medium hit CPU credit exhaustion under the full showcase stack; m7i.large was tight once the ColPali encoder container landed (~5 GB resident), so bumped to m7i.xlarge (4 vCPU / 16 GB) to keep the encoder, upload, search, and Firn comfortable on one box."
}

variable "git_repo" {
  type        = string
  description = "HTTPS git URL the EC2 instance clones at boot. The branch in var.git_branch must already be pushed."
}

variable "git_branch" {
  type        = string
  default     = "feat/multivector"
  description = "Branch the EC2 instance checks out. Tracks the active integration branch; bump to main once the work merges."
}

variable "firn_namespace" {
  type        = string
  default     = "images"
  description = "Firn namespace name. Multi-tenant deployments will use distinct values."
}

variable "vpc_id" {
  type        = string
  default     = "vpc-00bd949065ee5abe0"
  description = "Existing VPC in eu-west-1. The account is at its VPC-per-region quota, so this stack reuses an existing one."
}

variable "public_subnet_ids" {
  type        = list(string)
  default     = ["subnet-0fbc5328c0af9a3f5", "subnet-06a8dd0d8bb30de13"]
  description = "Public subnets (one per AZ) used by the ALB."
}

variable "private_subnet_ids" {
  type        = list(string)
  default     = ["subnet-02d67b789b9ba177d"]
  description = "Private subnet(s) with a NAT route. The EC2 instance lives here so it is not directly addressable from the internet."
}
