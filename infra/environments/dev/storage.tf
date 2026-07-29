# The data bucket: originals, derived data, and Firn's tables.
#
# One bucket with a clear prefix layout rather than several buckets. Lifecycle
# rules, metrics and costs are all expressible per prefix, and
# bucket-per-concern would multiply the IAM and Terraform surface without
# buying separation that prefixes do not already give.

resource "aws_s3_bucket" "data" {
  bucket = "metabare-${var.environment}-${data.aws_caller_identity.current.account_id}"

  # See var.bucket_force_destroy. A destroy that fails on a bucket the smoke
  # test just wrote to is not a working teardown, and a half-destroyed
  # environment is worse than either outcome.
  force_destroy = var.bucket_force_destroy

  tags = merge(local.tags, { Component = "storage" })
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket = aws_s3_bucket.data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      # SSE-S3, not SSE-KMS. KMS adds $0.03 per 10,000 requests, and Firn makes
      # a lot of small object reads: at scale that becomes a real line item for
      # a lab whose data is synthetic. Revisit before any real personal data
      # lands here, where the trade runs the other way.
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id

  versioning_configuration {
    # Off. Originals are content-addressed and derived data is reproducible,
    # so versioning would mostly store copies of things that cannot change.
    # Turning it on later changes item identity for everything written
    # afterwards, so it is a migration rather than a toggle.
    status = "Suspended"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  # A failed multipart upload leaves parts that are billed as storage and are
  # invisible in the console object listing. This is the single most commonly
  # forgotten S3 cost.
  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # Versioning is suspended, but noncurrent versions can exist from any period
  # when it was not. Bounded rather than accumulating silently.
  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  # Benchmark intermediates are large, reproducible, and worthless a week
  # later. Reports and manifests under benchmarks/reports/ are NOT covered by
  # this rule and are retained.
  rule {
    id     = "expire-benchmark-intermediates"
    status = "Enabled"

    filter {
      prefix = "benchmarks/input/"
    }

    expiration {
      days = 14
    }
  }

  # NOTE: no transition rules to colder storage classes yet, on purpose.
  # Whether they save money here is a question to measure, not to assume. The
  # working hypotheses are that Firn's tables and the model artefacts must stay
  # immediately retrievable, and that small derived objects cost more to
  # transition than they save. They are hypotheses, and they get tested on
  # copies before anything touches real data.
}

# Request metrics show GET/PUT/LIST activity by prefix. They cost $0.01 per
# million requests monitored, which is negligible here, and without them the
# storage cost story is guesswork.
resource "aws_s3_bucket_metric" "firn" {
  bucket = aws_s3_bucket.data.id
  name   = "firn-prefix"

  filter {
    prefix = "firn/"
  }
}

resource "aws_s3_bucket_metric" "raw" {
  bucket = aws_s3_bucket.data.id
  name   = "raw-prefix"

  filter {
    prefix = "raw/"
  }
}

resource "aws_s3_bucket_metric" "derived" {
  bucket = aws_s3_bucket.data.id
  name   = "derived-prefix"

  filter {
    prefix = "derived/"
  }
}
