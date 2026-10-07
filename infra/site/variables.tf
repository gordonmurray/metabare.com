variable "region" {
  description = "Region for the site bucket."
  type        = string
  default     = "eu-west-1"
}

variable "domain" {
  description = "Apex domain. Its Route 53 hosted zone must already exist."
  type        = string
  default     = "metabare.com"
}

variable "price_class" {
  description = "CloudFront price class. PriceClass_100 serves from North America and Europe only, the cheapest."
  type        = string
  default     = "PriceClass_100"
}

variable "budget_usd" {
  description = "Monthly budget for resources tagged Project=metabare. The Project cost allocation tag must be active in Billing for the filter to match."
  type        = string
  default     = "10"
}

variable "budget_alert_email" {
  description = "Where budget alerts go. Required: nothing else tells you the site costs more than expected."
  type        = string
}
