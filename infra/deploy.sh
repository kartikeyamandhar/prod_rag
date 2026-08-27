#!/usr/bin/env bash
# Deploy from laptop to the box: rsync repo + parquet artifacts, load DB, start API.
# Usage: infra/deploy.sh [ip]   (ip defaults to terraform output)
set -euo pipefail
cd "$(dirname "$0")/.."
# terraform output is stale after stop/start (public IP changes); resolve live.
resolve_ip() {
  aws ec2 describe-instances \
    --instance-ids "$(terraform -chdir=infra output -raw instance_id)" \
    --region "${AWS_REGION:-us-west-2}" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text
}
IP=${1:-$(resolve_ip)}
# accept-new is TOFU onto a possibly recycled AWS IP; acceptable for a lab box
# with nothing secret on it, noted deliberately.
SSH="ssh -o StrictHostKeyChecking=accept-new ec2-user@$IP"

echo "== waiting for first-boot provisioning =="
for _ in $(seq 1 60); do
  $SSH 'test -f /var/lib/cloud/instance/provisioned-ok' 2>/dev/null && break
  sleep 5
done
$SSH 'test -f /var/lib/cloud/instance/provisioned-ok'

echo "== rsync =="
rsync -az -e "ssh -o StrictHostKeyChecking=accept-new" \
  --exclude .git --exclude .venv --exclude .corpus_cache --exclude .env \
  --exclude .ruff_cache --exclude .pytest_cache --exclude __pycache__ \
  ./ "ec2-user@$IP:rag-incident-lab/"

echo "== remote setup =="
$SSH 'bash -s' <<'REMOTE'
set -euo pipefail
cd rag-incident-lab
umask 077
# Admin UIs stay loopback; reach via: ssh -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090
# Grafana password is generated per deploy; recover with: grep GRAFANA .env
cat > .env <<ENV
DATABASE_URL=postgresql://rag:rag_local_dev@127.0.0.1:5433/rag
POSTGRES_USER=rag
POSTGRES_PASSWORD=rag_local_dev
POSTGRES_DB=rag
POSTGRES_PORT=5433
BIND_HOST=127.0.0.1
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 16)
AWS_REGION=us-west-2
BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
EMBED_MODEL_NAME=BAAI/bge-small-en-v1.5
LLM_MAX_CONCURRENCY=3
LLM_ACQUIRE_TIMEOUT_S=0.25
BEDROCK_ENABLED=1
ENV
chmod 600 .env
docker compose up -d --wait postgres prometheus grafana
uv sync
# Single seed path: loads both corpora, resets replay state, re-hydrates PR
# ground truth, and asserts the DB fingerprint against the committed baseline.
uv run --env-file .env python -m probes.reset_corpus
sudo tee /etc/systemd/system/rag-api.service > /dev/null <<'UNIT'
[Unit]
Description=rag-incident-lab API
After=docker.service network-online.target
[Service]
User=ec2-user
WorkingDirectory=/home/ec2-user/rag-incident-lab
EnvironmentFile=/home/ec2-user/rag-incident-lab/.env
ExecStart=/usr/local/bin/uv run --env-file .env uvicorn api.main:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now rag-api
REMOTE

echo "== healthz from laptop =="
for _ in $(seq 1 30); do
  curl -s --max-time 5 "http://$IP:8080/healthz" && echo && exit 0
  sleep 2
done
echo "API did not come up" >&2
exit 1
