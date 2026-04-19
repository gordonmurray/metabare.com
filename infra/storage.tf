# Single bucket for both Lance table data and image binaries.
# CloudFront has read on lance/images/* via OAC; the dedicated app
# IAM user has CRUD on the whole bucket.
resource "aws_s3_bucket" "data" {
  bucket = local.data_bucket_name
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id

  versioning_configuration {
    # Off for now; flip to Enabled before production user uploads land.
    status = "Disabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # Deletes user-uploaded images 30 days after PutObject. The
  # upload sync tags new image objects with retention=auto-expire;
  # the existing seed set is untagged and kept indefinitely.
  rule {
    id     = "expire-user-uploads"
    status = "Enabled"

    filter {
      and {
        prefix = "lance/images/"
        tags = {
          retention = "auto-expire"
        }
      }
    }

    expiration {
      days = 30
    }
  }
}
