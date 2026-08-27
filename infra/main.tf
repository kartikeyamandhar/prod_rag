# rag-incident-lab: one t4g.medium in us-west-2, SG allowlisted to the home IP.
# AMI resolved via the SSM alias per CLAUDE.md; resolved id surfaced as an output
# and recorded in PHASES.md at first apply. Bedrock access comes from the instance
# role, so no AWS credential is ever copied to the box.

terraform {
  required_version = ">= 1.15.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "us-west-2"
}

variable "allowed_cidr" {
  type        = string
  description = "Home IP /32; the only source allowed by the security group"
}

variable "public_key_path" {
  type    = string
  default = "~/.ssh/id_ed25519.pub"
}

variable "instance_type" {
  type    = string
  default = "t4g.medium"
}

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_key_pair" "lab" {
  key_name   = "rag-incident-lab"
  public_key = file(var.public_key_path)
}

resource "aws_security_group" "lab" {
  name        = "rag-incident-lab"
  description = "home-IP allowlist: ssh, api, grafana, prometheus"

  # 3000/9090 (Grafana/Prometheus) are deliberately closed: admin UIs bind
  # loopback on the box and are reached via ssh -L port-forwarding only.
  dynamic "ingress" {
    for_each = [22, 8080]
    content {
      from_port   = ingress.value
      to_port     = ingress.value
      protocol    = "tcp"
      cidr_blocks = [var.allowed_cidr]
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_iam_role" "lab" {
  name = "rag-incident-lab"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "bedrock_invoke" {
  name = "bedrock-invoke"
  role = aws_iam_role.lab.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  # Break-glass access via SSM Session Manager: survives home-IP rotation,
  # and allows running with every ingress rule closed if needed.
  role       = aws_iam_role.lab.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "lab" {
  name = "rag-incident-lab"
  role = aws_iam_role.lab.name
}

resource "aws_instance" "lab" {
  ami                    = data.aws_ssm_parameter.al2023_arm64.value
  instance_type          = var.instance_type
  key_name               = aws_key_pair.lab.key_name
  vpc_security_group_ids = [aws_security_group.lab.id]
  iam_instance_profile   = aws_iam_instance_profile.lab.name
  user_data              = file("${path.module}/user_data.sh")

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
  }

  tags = {
    Name    = "rag-incident-lab"
    project = "rag-incident-lab"
  }
}

output "instance_id" {
  value = aws_instance.lab.id
}

output "public_ip" {
  value = aws_instance.lab.public_ip
}

output "resolved_ami" {
  value     = data.aws_ssm_parameter.al2023_arm64.value
  sensitive = true
}
