# Ingestion queues.
#
# Two queues, each with a dead-letter queue. The CPU queue is fed by S3
# ObjectCreated events; the GPU queue is fed by CPU workers and is what KEDA
# will scale on once the GPU path exists.
#
# Created now, before the workers that consume them, because the S3 event
# notification and its queue policy are part of the storage layer and are
# easier to get right in one apply than to retrofit.

locals {
  # The visibility timeout must exceed normal processing time.
  # OCR on a large screenshot is the slow case on the CPU path; five minutes is
  # generous for it and short enough that a crashed worker's message returns
  # promptly. Workers extend it for genuinely long work rather than relying on
  # this being large enough.
  cpu_visibility_timeout = 300

  # The GPU path processes batches after a cold start that can itself take
  # minutes. A message must not become visible again while the node that
  # claimed it is still booting.
  gpu_visibility_timeout = 900

  # Long polling. Short polling would bill an empty receive every second for a
  # queue that is idle almost all of the time.
  receive_wait_time = 20
}

resource "aws_sqs_queue" "cpu_dlq" {
  name                      = "${local.name}-cpu-dlq"
  message_retention_seconds = 1209600 # 14 days, the maximum

  tags = merge(local.tags, { Component = "ingestion", Role = "dead-letter" })
}

resource "aws_sqs_queue" "cpu" {
  name                       = "${local.name}-cpu"
  visibility_timeout_seconds = local.cpu_visibility_timeout
  receive_wait_time_seconds  = local.receive_wait_time
  message_retention_seconds  = 345600 # 4 days

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.cpu_dlq.arn
    # Five attempts, not three: a Spot interruption mid-batch should not spend
    # a third of an item's budget. Poison messages still reach the DLQ quickly
    # enough to notice.
    maxReceiveCount = 5
  })

  tags = merge(local.tags, { Component = "ingestion" })
}

resource "aws_sqs_queue" "gpu_dlq" {
  name                      = "${local.name}-gpu-dlq"
  message_retention_seconds = 1209600

  tags = merge(local.tags, { Component = "gpu-ingestion", Role = "dead-letter" })
}

resource "aws_sqs_queue" "gpu" {
  name                       = "${local.name}-gpu"
  visibility_timeout_seconds = local.gpu_visibility_timeout
  receive_wait_time_seconds  = local.receive_wait_time
  message_retention_seconds  = 345600

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.gpu_dlq.arn
    maxReceiveCount     = 5
  })

  tags = merge(local.tags, { Component = "gpu-ingestion" })
}

# Let S3 publish ObjectCreated events for the raw prefixes into the CPU queue.
data "aws_iam_policy_document" "cpu_queue" {
  statement {
    sid    = "AllowS3Notifications"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }

    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.cpu.arn]

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_s3_bucket.data.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_sqs_queue_policy" "cpu" {
  queue_url = aws_sqs_queue.cpu.id
  policy    = data.aws_iam_policy_document.cpu_queue.json
}

# Disabled until a CPU worker exists to consume the queue. See
# var.enable_s3_notifications: an enabled notification with no consumer loses
# every event after the queue's four-day retention, while looking healthy.
resource "aws_s3_bucket_notification" "raw_objects" {
  count = var.enable_s3_notifications ? 1 : 0

  bucket = aws_s3_bucket.data.id

  # Only the raw prefixes. Notifying on derived/ would make the pipeline
  # trigger itself: a worker writing derived output would enqueue work that
  # produces more derived output.
  queue {
    queue_arn     = aws_sqs_queue.cpu.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "raw/screenshots/"
  }

  queue {
    queue_arn     = aws_sqs_queue.cpu.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "raw/notes/"
  }

  depends_on = [aws_sqs_queue_policy.cpu]
}
