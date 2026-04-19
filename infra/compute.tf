resource "aws_instance" "main" {
  ami                         = data.aws_ami.al2023.id
  instance_type               = var.instance_type
  iam_instance_profile        = aws_iam_instance_profile.instance.name
  vpc_security_group_ids      = [aws_security_group.instance.id]
  subnet_id                   = var.private_subnet_ids[0]
  associate_public_ip_address = false

  user_data = templatefile("${path.module}/user-data.sh.tpl", {
    region         = var.region
    git_repo       = var.git_repo
    git_branch     = var.git_branch
    bucket         = aws_s3_bucket.data.bucket
    cdn_domain     = var.cdn_domain
    firn_namespace = var.firn_namespace
  })
  # Don't replace on user-data change: the bootstrap is only relevant
  # for a fresh instance. Code updates go via `git pull` + `docker
  # compose up` on the running instance.
  user_data_replace_on_change = false

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

  depends_on = [
    aws_iam_role_policy.instance_s3,
    aws_s3_bucket.data,
  ]
}
