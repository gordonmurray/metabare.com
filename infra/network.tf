# Existing VPC (the cloudfloe account is at its VPC quota). Routing
# and internet gateway are already managed outside this stack.
data "aws_vpc" "main" {
  id = var.vpc_id
}

data "aws_subnet" "public" {
  for_each = toset(var.public_subnet_ids)
  id       = each.key
}

resource "aws_security_group" "alb" {
  name        = "metabare-alb"
  description = "Public 80/443 ingress for the metabare ALB"
  vpc_id      = data.aws_vpc.main.id

  ingress {
    description = "https"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "http (redirected to https at the listener)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "instance" {
  name        = "metabare-instance"
  description = "Frontend port from ALB SG only; egress unrestricted for SSM and image pulls"
  vpc_id      = data.aws_vpc.main.id

  ingress {
    description     = "frontend nginx (target group port)"
    from_port       = 8082
    to_port         = 8082
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
