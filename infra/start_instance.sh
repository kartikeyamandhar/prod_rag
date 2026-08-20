#!/usr/bin/env bash
# Start the stopped box. The public IP CHANGES on stop/start; prints the new one.
set -euo pipefail
cd "$(dirname "$0")"
ID=$(terraform output -raw instance_id)
REGION=${AWS_REGION:-us-west-2}
aws ec2 start-instances --instance-ids "$ID" --region "$REGION" --output text
aws ec2 wait instance-running --instance-ids "$ID" --region "$REGION"
aws ec2 describe-instances --instance-ids "$ID" --region "$REGION" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text
