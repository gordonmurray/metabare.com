# EC2 instance role. Grants s3:{Get,Put,Delete,List}Object on the
# data bucket plus SSM Session Manager. No static access keys live
# anywhere in the stack; Firn's object_store, boto3, and lancedb-rs
# all fall through to the default AWS credential chain (IMDS).
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

resource "aws_iam_instance_profile" "instance" {
  name = "metabare-instance"
  role = aws_iam_role.instance.name
}
