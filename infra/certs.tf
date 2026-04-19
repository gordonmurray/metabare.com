# Cert for the ALB (apex + www). Lives in eu-west-1 with the ALB.
resource "aws_acm_certificate" "alb" {
  domain_name               = var.domain
  subject_alternative_names = ["www.${var.domain}"]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_acm_certificate_validation" "alb" {
  certificate_arn = aws_acm_certificate.alb.arn
}

# Cert for CloudFront. Must be in us-east-1.
resource "aws_acm_certificate" "cdn" {
  provider = aws.useast1

  domain_name       = var.cdn_domain
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_acm_certificate_validation" "cdn" {
  provider        = aws.useast1
  certificate_arn = aws_acm_certificate.cdn.arn
}
