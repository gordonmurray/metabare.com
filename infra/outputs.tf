output "alb_dns_name" {
  value       = aws_lb.main.dns_name
  description = "Point the apex (and www) at this DNS name via CNAME or ALIAS"
}

output "cloudfront_domain" {
  value       = aws_cloudfront_distribution.cdn.domain_name
  description = "Point the CDN host at this DNS name via CNAME"
}

output "acm_validation_records_alb" {
  value = {
    for o in aws_acm_certificate.alb.domain_validation_options :
    o.domain_name => {
      name  = o.resource_record_name
      type  = o.resource_record_type
      value = o.resource_record_value
    }
  }
  description = "Add these CNAMEs at the DNS provider to validate the ALB cert"
}

output "acm_validation_records_cdn" {
  value = {
    for o in aws_acm_certificate.cdn.domain_validation_options :
    o.domain_name => {
      name  = o.resource_record_name
      type  = o.resource_record_type
      value = o.resource_record_value
    }
  }
  description = "Add these CNAMEs at the DNS provider to validate the CloudFront cert"
}

output "instance_id" {
  value       = aws_instance.main.id
  description = "Use with: aws ssm start-session --target <id>"
}

output "data_bucket" {
  value = aws_s3_bucket.data.bucket
}

output "app_secret_id" {
  value       = aws_secretsmanager_secret.app_creds.name
  description = "Secrets Manager secret holding S3 creds for the app containers"
}
