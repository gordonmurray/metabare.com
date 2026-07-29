# Container registry.
#
# Managed here rather than created ad hoc by the deploy script, for two
# reasons. A repository created as a side effect of a script is invisible to
# `make destroy`, so it survives teardown and keeps billing. And without a
# lifecycle policy every deployment adds a ~128 MB image that nothing ever
# reclaims: at a few deployments a day that is gigabytes a month of storage
# for images no one will run again.

resource "aws_ecr_repository" "api" {
  name = "metabare/api"

  # Immutable tags: a tag always refers to the same bytes, so a rollback to a
  # tag is a rollback to a known artefact rather than to whatever was last
  # pushed under that name.
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  # The bucket cannot be destroyed while it holds images, and a lab
  # environment is meant to be destroyable. Images are rebuildable from the
  # source commit they are tagged with.
  force_delete = true

  tags = merge(local.tags, { Component = "registry" })
}

resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images quickly; they are build residue."
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep only the most recent images."
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = var.ecr_image_retention_count
        }
        action = { type = "expire" }
      },
    ]
  })
}
