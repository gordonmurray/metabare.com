# Partial backend configuration. Bucket, key and profile are supplied at init
# time, so this repository names nobody's private state bucket and anyone can
# point the same code at their own.
#
#   cp backend.hcl.example backend.hcl    # edit it
#   make init ENV=dev
#
# Or directly:
#
#   terraform init -backend-config=backend.hcl
#
# backend.hcl is gitignored; backend.hcl.example is the template.
#
# One hard requirement when adapting this: use a state key nothing else writes
# to. Sharing a bucket between environments is fine. Sharing a key means one
# apply can destroy another environment's resources.
terraform {
  backend "s3" {}
}
