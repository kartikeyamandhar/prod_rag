#!/usr/bin/env bash
# Weekend discipline: stop (never terminate) the box. EBS still bills while stopped.
set -euo pipefail
cd "$(dirname "$0")"
ID=$(terraform output -raw instance_id)
REGION=${AWS_REGION:-us-west-2}
aws ec2 stop-instances --instance-ids "$ID" --region "$REGION" --output text
aws ec2 wait instance-stopped --instance-ids "$ID" --region "$REGION"
aws ec2 describe-instances --instance-ids "$ID" --region "$REGION" \
  --query 'Reservations[0].Instances[0].State.Name' --output text
