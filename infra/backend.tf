terraform {
  backend "s3" {
    bucket       = "metabare-tfstate-358b07cf"
    key          = "metabare/prod.tfstate"
    region       = "eu-west-1"
    profile      = "cloudfloe"
    encrypt      = true
    use_lockfile = true
  }
}
