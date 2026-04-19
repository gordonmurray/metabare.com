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
  type    = string
  default = "t3.medium"
}

variable "git_repo" {
  type        = string
  description = "HTTPS git URL the EC2 instance clones at boot. The branch in var.git_branch must already be pushed."
}

variable "git_branch" {
  type        = string
  default     = "firn-integration"
  description = "Branch the EC2 instance checks out"
}

variable "firn_namespace" {
  type        = string
  default     = "images"
  description = "Firn namespace name. Multi-tenant deployments will use distinct values."
}
