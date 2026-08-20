#!/usr/bin/env bash
# Residual-billable-resource check, run every weekend and at project end.
# Anything printed under a header is billing or can bill; empty sections are clean.
set -euo pipefail
REGION=${AWS_REGION:-us-west-2}
echo "== EC2 instances (non-terminated; stopped still bills EBS) =="
aws ec2 describe-instances --region "$REGION" \
  --query 'Reservations[].Instances[?State.Name!=`terminated`].[InstanceId,InstanceType,State.Name]' \
  --output text
echo "== EBS volumes =="
aws ec2 describe-volumes --region "$REGION" --query 'Volumes[].[VolumeId,Size,State]' --output text
echo "== Elastic IPs (bill when unattached) =="
aws ec2 describe-addresses --region "$REGION" --query 'Addresses[].[PublicIp,AssociationId]' --output text
echo "== NAT gateways =="
aws ec2 describe-nat-gateways --region "$REGION" \
  --query 'NatGateways[?State!=`deleted`].[NatGatewayId,State]' --output text
echo "== snapshots owned by me =="
aws ec2 describe-snapshots --owner-ids self --region "$REGION" \
  --query 'Snapshots[].[SnapshotId,VolumeSize]' --output text
