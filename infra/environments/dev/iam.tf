# Workload identity.
#
# EKS Pod Identity rather than IRSA. Both avoid static credentials, which is
# the requirement; Pod Identity is chosen because the trust
# policy is a plain service principal rather than an OIDC condition string
# containing the cluster's issuer URL, so a role survives the cluster being
# destroyed and recreated. Given that `make destroy` between sessions is the
# intended workflow here, that matters more than it usually would.

data "aws_iam_policy_document" "pod_identity_trust" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }

    actions = [
      "sts:AssumeRole",
      "sts:TagSession",
    ]
  }
}

# --- Firn ------------------------------------------------------------------
#
# Firn owns the firn/ prefix and nothing else. It never reads originals or
# derived metadata, so it is not granted access to them.

resource "aws_iam_role" "firn" {
  name               = "${local.name}-firn"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json
  tags               = merge(local.tags, { Component = "firn" })
}

data "aws_iam_policy_document" "firn" {
  statement {
    sid    = "ObjectAccessWithinFirnPrefix"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      # Firn uses conditional writes (If-None-Match) for compare-and-swap
      # safety between concurrent writers. That is a PutObject with a
      # precondition, so it needs no extra action, but GetObjectAttributes is
      # used to read the current version cheaply.
      "s3:GetObjectAttributes",
    ]
    resources = ["${aws_s3_bucket.data.arn}/firn/*"]
  }

  statement {
    sid       = "ListOnlyTheFirnPrefix"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["firn/*", "firn/"]
    }
  }
}

resource "aws_iam_role_policy" "firn" {
  name   = "s3-firn-prefix"
  role   = aws_iam_role.firn.id
  policy = data.aws_iam_policy_document.firn.json
}

resource "aws_eks_pod_identity_association" "firn" {
  cluster_name    = module.eks.cluster_name
  namespace       = "metabare"
  service_account = "firn"
  role_arn        = aws_iam_role.firn.arn
  tags            = local.tags
}

# --- API -------------------------------------------------------------------
#
# The API reads everything (to hydrate search results and serve items) and
# writes originals and derived data (to ingest a note directly). It has no
# queue permissions: it does not consume, and it does not need to enqueue
# because S3 events do that.

resource "aws_iam_role" "api" {
  name               = "${local.name}-api"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json
  tags               = merge(local.tags, { Component = "api" })
}

data "aws_iam_policy_document" "api" {
  statement {
    sid    = "ReadAndWriteApplicationData"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = [
      "${aws_s3_bucket.data.arn}/raw/*",
      "${aws_s3_bucket.data.arn}/derived/*",
    ]
  }

  # Delete is scoped to record documents only. Originals are immutable and
  # nothing in the application deletes them, so granting
  # delete over raw/ would be a permission that exists solely to make a bug or
  # a compromise more destructive. The only delete the code performs is
  # retiring record documents when a re-index produces fewer chunks
  # (ObjectStore.delete_record).
  statement {
    sid       = "DeleteOnlyRecordDocuments"
    effect    = "Allow"
    actions   = ["s3:DeleteObject"]
    resources = ["${aws_s3_bucket.data.arn}/derived/records/*"]
  }

  # HeadBucket, which /readyz uses, is authorised by s3:ListBucket. There is
  # no narrower action for it, and the permission grants no object access.
  statement {
    sid       = "ListBucketForReadinessProbe"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "s3-application-data"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

resource "aws_eks_pod_identity_association" "api" {
  cluster_name    = module.eks.cluster_name
  namespace       = "metabare"
  service_account = "api"
  role_arn        = aws_iam_role.api.arn
  tags            = local.tags
}

# --- CPU ingestion worker --------------------------------------------------

resource "aws_iam_role" "cpu_worker" {
  name               = "${local.name}-cpu-worker"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json
  tags               = merge(local.tags, { Component = "cpu-worker" })
}

data "aws_iam_policy_document" "cpu_worker" {
  # Read originals, write derived data. It never needs to write to raw/,
  # because by the time a worker sees an item the original is already stored.
  statement {
    sid       = "ReadOriginals"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data.arn}/raw/*"]
  }

  statement {
    sid    = "ReadWriteDerivedData"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = ["${aws_s3_bucket.data.arn}/derived/*"]
  }

  statement {
    sid       = "DeleteOnlyRecordDocuments"
    effect    = "Allow"
    actions   = ["s3:DeleteObject"]
    resources = ["${aws_s3_bucket.data.arn}/derived/records/*"]
  }

  statement {
    sid       = "ListBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
  }

  statement {
    sid    = "ConsumeCpuQueue"
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      # Extending visibility for genuinely long work, rather than setting a
      # visibility timeout large enough to cover the worst case.
      "sqs:ChangeMessageVisibility",
    ]
    resources = [aws_sqs_queue.cpu.arn]
  }

  statement {
    sid       = "EnqueueGpuWork"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.gpu.arn]
  }
}

resource "aws_iam_role_policy" "cpu_worker" {
  name   = "ingestion"
  role   = aws_iam_role.cpu_worker.id
  policy = data.aws_iam_policy_document.cpu_worker.json
}

resource "aws_eks_pod_identity_association" "cpu_worker" {
  cluster_name    = module.eks.cluster_name
  namespace       = "metabare"
  service_account = "cpu-worker"
  role_arn        = aws_iam_role.cpu_worker.arn
  tags            = local.tags
}
