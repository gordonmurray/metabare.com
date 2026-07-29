output "cluster_name" {
  description = "EKS cluster name."
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "EKS API server endpoint."
  value       = module.eks.cluster_endpoint
}

output "region" {
  description = "AWS region."
  value       = var.region
}

output "data_bucket" {
  description = "S3 bucket holding originals, derived data and Firn's tables."
  value       = aws_s3_bucket.data.id
}

output "cpu_queue_url" {
  description = "SQS queue fed by S3 ObjectCreated events."
  value       = aws_sqs_queue.cpu.url
}

output "gpu_queue_url" {
  description = "SQS queue for GPU embedding work. KEDA will scale on this once the GPU path exists."
  value       = aws_sqs_queue.gpu.url
}

output "kubeconfig_command" {
  description = "Command to configure kubectl for this cluster."
  value       = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.region}${var.aws_profile == "" ? "" : " --profile ${var.aws_profile}"}"
}

# The major fixed-cost resources, as an output. Deliberately blunt: a number
# someone has to look at is harder to forget than a line in a document.
#
# Prices are eu-west-1 list prices from the AWS Price List API, queried
# 2026-07-29. Reproduce with scripts/aws-prices.py. They exclude VAT, Savings
# Plans, credits and the free tier, and they are estimates, not billed amounts.
output "fixed_cost_resources" {
  description = "Fixed monthly cost of this environment, itemised. Estimates, not invoice values."
  value = {
    priced_on = "2026-07-29"
    region    = var.region
    currency  = "USD"

    items = [
      {
        resource = "EKS control plane"
        detail   = "1 cluster at $0.10/hour"
        monthly  = 73.00
        teardown = "make destroy ENV=${var.environment}"
      },
      {
        resource = "Stable node group"
        detail   = "1 x ${var.stable_node_instance_type} On-Demand"
        monthly  = var.stable_node_instance_type == "t3.large" ? 66.58 : -1
        teardown = "make destroy ENV=${var.environment}"
      },
      {
        resource = "EBS root volume"
        detail   = "${var.stable_node_disk_gb} GiB gp3 at $0.088/GB-month"
        monthly  = var.stable_node_disk_gb * 0.088
        teardown = "destroyed with the node"
      },
      {
        resource = "NAT Gateway"
        detail   = var.enable_nat_gateway ? "ENABLED at $0.048/hour plus $0.048/GB" : "not created; an S3 gateway endpoint is free"
        monthly  = var.enable_nat_gateway ? 35.04 : 0.00
        teardown = "set enable_nat_gateway = false"
      },
      {
        resource = "S3 gateway endpoint"
        detail   = "not billed"
        monthly  = 0.00
        teardown = "make destroy ENV=${var.environment}"
      },
      {
        resource = "SQS queues and DLQs"
        detail   = "4 queues; first 1M requests/month free"
        monthly  = 0.00
        teardown = "make destroy ENV=${var.environment}"
      },
      {
        resource = "KMS customer-managed key"
        detail   = "EKS secret envelope encryption, created by the EKS module. $1/month per key plus $0.03 per 10,000 requests"
        monthly  = 1.00
        teardown = "make destroy ENV=${var.environment}; the key enters a pending-deletion window rather than disappearing"
      },
      {
        resource = "CloudWatch log group"
        detail   = "/aws/eks/${local.name}/cluster, 90-day retention. Free while enabled_log_types is empty; turning control-plane logging on costs $0.57/GB ingested"
        monthly  = 0.00
        teardown = "make destroy ENV=${var.environment}"
      },
      {
        resource = "ECR image storage"
        detail   = "$0.10/GB-month. Each deployment pushes a ~128 MB image; a lifecycle policy keeps ${var.ecr_image_retention_count} and expires the rest"
        monthly  = 0.13
        teardown = "make destroy ENV=${var.environment} (force_delete is on)"
      },
      {
        resource = "S3 storage and requests"
        detail   = "usage-based, roughly $0.63/month at 10 GB and 200k requests"
        monthly  = 0.63
        teardown = "empty the bucket, then make destroy"
      },
    ]

    estimated_monthly_total = 73.00 + (var.stable_node_instance_type == "t3.large" ? 66.58 : 0) + (var.stable_node_disk_gb * 0.088) + (var.enable_nat_gateway ? 35.04 : 0) + 1.00 + 0.13 + 0.63

    note = "Idle cost. Accrues whether or not anything is ingested. Destroying between sessions is the intended workflow; see the cost table in the README."
  }
}

output "account_id" {
  description = "Account this environment was applied into. The deploy script refuses to run against a different one."
  value       = data.aws_caller_identity.current.account_id
}

output "aws_profile" {
  description = "Profile Terraform used. The deploy script exports this so the AWS CLI cannot silently resolve to a different account."
  value       = var.aws_profile
}

output "ecr_repository_url" {
  description = "ECR repository for the API image."
  value       = aws_ecr_repository.api.repository_url
}

# Whether the budget guardrail actually exists. Terraform cannot invent an
# address to notify, so the budget is opt-in, and an opt-in guardrail that
# nobody notices they skipped is not a guardrail. This makes it visible.
output "budget_alerting" {
  description = "Whether an AWS Budget with alerts was created. False means nothing will tell you this environment is still running."
  value = var.budget_alert_email == "" ? {
    enabled = false
    detail  = "No budget created. Set budget_alert_email to get alerts at 80% actual and 100% forecast spend."
    } : {
    enabled = true
    detail  = "Alerting ${var.budget_alert_email} at 80% of $${var.monthly_budget_usd} actual and 100% forecast."
  }
}
