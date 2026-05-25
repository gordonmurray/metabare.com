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
    volume_size = 90
    volume_type = "gp3"
    encrypted   = true
  }

  tags = {
    Name = "metabare-${var.env}"
  }

  # The data.aws_ami.al2023 lookup uses most_recent = true, so AWS
  # publishing a new AL2023 revision would otherwise force-replace
  # the instance on every plan. AMI upgrades on this stack are a
  # deliberate operation: remove this block temporarily and apply,
  # or `terraform taint` the resource. Routine security patches go
  # in-place via `sudo dnf update -y` + reboot.
  lifecycle {
    ignore_changes = [ami]
  }

  depends_on = [
    aws_iam_role_policy.instance_s3,
    aws_s3_bucket.data,
  ]
}
