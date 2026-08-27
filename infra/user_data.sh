#!/bin/bash
# First-boot provisioning for AL2023 arm64: docker + compose plugin + uv.
set -euxo pipefail
dnf install -y docker git rsync
systemctl enable --now docker
usermod -aG docker ec2-user
mkdir -p /usr/local/lib/docker/cli-plugins
# Pinned versions: "latest" at first boot violated the pin-everything rule.
curl -fsSL https://github.com/docker/compose/releases/download/v5.1.0/docker-compose-linux-aarch64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
curl -LsSf https://astral.sh/uv/0.11.21/install.sh | UV_INSTALL_DIR=/usr/local/bin sh
touch /var/lib/cloud/instance/provisioned-ok
