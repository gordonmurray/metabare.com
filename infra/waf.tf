# Regional Web ACL attached to the ALB. Two rate-based limits
# (tight on /upload, general on everything else) plus AWS's
# managed common + known-bad-inputs rule sets.
resource "aws_wafv2_web_acl" "main" {
  name        = "metabare"
  scope       = "REGIONAL"
  description = "Rate-limit /upload and /search, block obvious nasties"

  default_action {
    allow {}
  }

  # Tight limit on /upload. Each successful upload does CLIP
  # inference plus an S3 PutObject, so the blast radius of abuse
  # is higher here than on read paths.
  rule {
    name     = "rate-limit-upload"
    priority = 10

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 60
        aggregate_key_type = "IP"

        scope_down_statement {
          byte_match_statement {
            positional_constraint = "STARTS_WITH"
            search_string         = "/upload"

            field_to_match {
              uri_path {}
            }

            text_transformation {
              priority = 0
              type     = "NONE"
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "rate-limit-upload"
      sampled_requests_enabled   = true
    }
  }

  # Broad per-IP cap across the whole site. Generous enough that a
  # real visitor browsing dashboards and running searches won't
  # trip it, tight enough to absorb a runaway scraper.
  rule {
    name     = "rate-limit-general"
    priority = 20

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 2000
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "rate-limit-general"
      sampled_requests_enabled   = true
    }
  }

  # OWASP-lite baseline: SQLi, XSS, path traversal attempts.
  # A handful of rules in the managed set conflict with binary
  # file uploads (the 8 KB body-inspection limit and body-XSS
  # regex pattern trip on JPEG bytes), so they are switched to
  # count mode; everything else still blocks.
  rule {
    name     = "aws-managed-common"
    priority = 30

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"

        rule_action_override {
          name = "SizeRestrictions_BODY"
          action_to_use {
            count {}
          }
        }

        rule_action_override {
          name = "CrossSiteScripting_BODY"
          action_to_use {
            count {}
          }
        }

        rule_action_override {
          name = "GenericRFI_BODY"
          action_to_use {
            count {}
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "aws-managed-common"
      sampled_requests_enabled   = true
    }
  }

  # Blocks requests matching known malicious signatures AWS curates.
  rule {
    name     = "aws-managed-known-bad-inputs"
    priority = 40

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "aws-managed-known-bad-inputs"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "metabare-waf"
    sampled_requests_enabled   = true
  }
}

resource "aws_wafv2_web_acl_association" "alb" {
  resource_arn = aws_lb.main.arn
  web_acl_arn  = aws_wafv2_web_acl.main.arn
}
