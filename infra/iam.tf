# Dedicated IAM user the application containers (Firn, upload, search)
# use to talk to S3. Stored in Secrets Manager and pulled at boot by
# user-data into /opt/metabare/.env. Could be replaced by an instance
# role plus boto3 default chain in P3 once Firn confirms IAM-role
# credential resolution.
resource "aws_iam_user" "app" {
  name = "metabare-${var.env}-app"
  path = "/service/"
}

resource "aws_iam_access_key" "app" {
  user = aws_iam_user.app.name
}

resource "aws_iam_user_policy" "app_s3" {
  name = "metabare-app-s3"
  user = aws_iam_user.app.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.data.arn,
          "${aws_s3_bucket.data.arn}/*",
        ]
      },
    ]
  })
}

resource "aws_secretsmanager_secret" "app_creds" {
  name                    = "metabare/${var.env}/app-creds"
  description             = "S3 access key/secret used by Firn and the upload/search containers"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "app_creds" {
  secret_id = aws_secretsmanager_secret.app_creds.id
  secret_string = jsonencode({
    AWS_ACCESS_KEY_ID     = aws_iam_access_key.app.id
    AWS_SECRET_ACCESS_KEY = aws_iam_access_key.app.secret
    S3_BUCKET             = aws_s3_bucket.data.bucket
    S3_REGION             = var.region
  })
}

# EC2 instance role: SSM Session Manager + read the app secret.
resource "aws_iam_role" "instance" {
  name = "metabare-instance"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Instance-role grant used by Firn, upload, and search via IMDS.
# The Rust object_store client and boto3 both fall through to the
# default AWS credential chain when no explicit keys are supplied,
# so no static access keys live in env or Secrets Manager.
resource "aws_iam_role_policy" "instance_s3" {
  name = "metabare-instance-s3"
  role = aws_iam_role.instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.data.arn,
          "${aws_s3_bucket.data.arn}/*",
        ]
      },
    ]
  })
}

resource "aws_iam_role_policy" "instance_secrets" {
  name = "metabare-instance-secrets"
  role = aws_iam_role.instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.app_creds.arn
      },
    ]
  })
}

resource "aws_iam_instance_profile" "instance" {
  name = "metabare-instance"
  role = aws_iam_role.instance.name
}
