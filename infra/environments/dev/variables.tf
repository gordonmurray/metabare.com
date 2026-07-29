variable "region" {
  description = "AWS region. eu-west-1 by default, but availability and price should be checked at deployment time."
  type        = string
  default     = "eu-west-1"
}

variable "aws_profile" {
  description = "AWS CLI profile for the provider. Empty (the default) uses the standard credential chain, including the AWS_PROFILE environment variable."
  type        = string
  default     = ""
}

variable "environment" {
  description = "Environment name. Used in resource names and cost allocation tags."
  type        = string
  default     = "dev"
}

variable "owner" {
  description = "Owner tag. Configurable so the repository contains no personal identifier."
  type        = string
  default     = "metabare"
}

variable "vpc_cidr" {
  description = "VPC CIDR. Chosen to avoid the ranges already in use in this account (10.0.0.0/16, 100.10.0.0/16, 172.31.0.0/16)."
  type        = string
  default     = "10.42.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "vpc_cidr must be a valid IPv4 CIDR block."
  }
}

variable "availability_zone_count" {
  description = "Number of AZs for subnets. EKS requires at least two for the control plane, even though nodes run in one."
  type        = number
  default     = 2

  validation {
    condition     = var.availability_zone_count >= 2
    error_message = "EKS requires subnets in at least two availability zones."
  }
}

variable "enable_nat_gateway" {
  description = <<-EOT
    Create a NAT Gateway and move nodes to private subnets.

    FALSE in dev, deliberately. A NAT Gateway is $0.048/hour in eu-west-1,
    roughly $35.04/month before data charges, which is about a quarter of this
    environment's entire cost, for a workload whose main dependency is S3 and
    which reaches S3 free over a gateway endpoint.

    A production environment would set this to true and accept the cost.
  EOT
  type        = bool
  default     = false
}

variable "kubernetes_version" {
  description = "EKS control plane version. 1.36 is the current default in eu-west-1 as of 2026-07-29."
  type        = string
  default     = "1.36"
}

variable "stable_node_instance_type" {
  description = <<-EOT
    Instance type for the always-on system node group.

    t3.large: 2 vCPU, 8 GiB, $0.0912/hour, about $66.58/month. It must hold
    Firn, the API, Karpenter, KEDA, CoreDNS and eventually a Prometheus stack.
    8 GiB is workable but not generous; if Prometheus pushes it over, the
    honest options are shorter retention or a larger node, and the choice
    should be made from measurements rather than in advance.
  EOT
  type        = string
  default     = "t3.large"
}

variable "stable_node_disk_gb" {
  description = "Root volume size in GiB. Holds container images and Firn's disposable object cache. gp3 at $0.088/GB-month."
  type        = number
  default     = 40
}

variable "monthly_budget_usd" {
  description = "AWS Budget threshold. Alerts at 80% actual and 100% forecast. Set this deliberately: it is the backstop against a runaway benchmark."
  type        = number
  default     = 200
}

variable "budget_alert_email" {
  description = "Where budget alerts go. Leave empty to skip creating the budget, though that removes a guardrail worth having."
  type        = string
  default     = ""
}

variable "enable_s3_notifications" {
  description = <<-EOT
    Publish S3 ObjectCreated events for raw/ into the CPU queue.

    FALSE until a CPU worker is actually deployed. An enabled notification with
    no consumer is worse than no notification: messages accumulate and are
    silently discarded when the queue's four-day retention expires, so the
    system looks healthy while losing every ingestion event.

    Turn this on in the same change that deploys the worker.
  EOT
  type        = bool
  default     = false
}

variable "bucket_force_destroy" {
  description = <<-EOT
    Allow Terraform to delete the data bucket while it still holds objects.

    TRUE here because this environment is a lab holding synthetic data, and a
    teardown path that fails the moment the system has been used is not a
    teardown path. Without it, `terraform destroy` errors on a non-empty
    bucket and leaves the environment half-removed and still billing.

    Set it FALSE for any environment holding data you would miss. The trade is
    real: with this on, a destroy takes the data with it and does not ask.
  EOT
  type        = bool
  default     = true
}

variable "ecr_image_retention_count" {
  description = "Number of container images to keep in ECR. Older ones are expired, because every deployment pushes a ~128 MB image and nothing else reclaims them."
  type        = number
  default     = 10
}

variable "cluster_admin_principals" {
  description = "IAM principal ARNs granted cluster admin via EKS access entries. Empty means only the creating principal has access."
  type        = list(string)
  default     = []
}
