output "bucket" {
  description = "Site bucket; scripts/deploy.sh uploads here."
  value       = aws_s3_bucket.site.bucket
}

output "distribution_id" {
  description = "CloudFront distribution; scripts/deploy.sh invalidates the entry pages here."
  value       = aws_cloudfront_distribution.site.id
}

output "url" {
  value = "https://${var.domain}"
}
