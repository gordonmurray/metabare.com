resource "aws_instance" "main" {
  ami                         = data.aws_ami.al2023.id
  instance_type               = var.instance_type
  iam_instance_profile        = aws_iam_instance_profile.instance.name
  vpc_security_group_ids      = [aws_security_group.instance.id]
  subnet_id                   = var.private_subnet_ids[0]
  associate_public_ip_address = false

  user_data = templatefile("${path.module}/user-data.sh.tpl", {
    region         = var.region
    secret_id      = aws_secretsmanager_secret.app_creds.name
    git_repo       = var.git_repo
    git_branch     = var.git_branch
    bucket         = aws_s3_bucket.data.bucket
    cdn_domain     = var.cdn_domain
    firn_namespace = var.firn_namespace
  })
  user_data_replace_on_change = true

  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
    encrypted   = true
  }

  tags = {
    Name = "metabare-${var.env}"
  }

  # IAM/secret/bucket need to exist before the EC2 boots; otherwise
  # user-data will fail to fetch credentials.
  depends_on = [
    aws_iam_role_policy.instance_secrets,
    aws_secretsmanager_secret_version.app_creds,
    aws_s3_bucket.data,
  ]
}
