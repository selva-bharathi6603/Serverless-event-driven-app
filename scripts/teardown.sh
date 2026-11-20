#!/usr/bin/env bash
# Deletes everything so you are not billed. Usage: ./scripts/teardown.sh
set -euo pipefail

STACK_NAME="${STACK_NAME:-serverless-demo}"
REGION="${AWS_REGION:-ap-south-1}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
UPLOADS="${STACK_NAME}-uploads-${ACCOUNT_ID}-${REGION}"
ARTIFACTS="${STACK_NAME}-artifacts-${ACCOUNT_ID}-${REGION}"

# A bucket must be empty before CloudFormation can delete it.
aws s3 rm "s3://$UPLOADS" --recursive --region "$REGION" || true

aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$REGION"
aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" --region "$REGION"

# Secrets are kept for 30 days by default, which blocks re-deploying with the same name.
aws secretsmanager delete-secret --secret-id "${STACK_NAME}/app-secrets" \
  --force-delete-without-recovery --region "$REGION" || true

aws s3 rb "s3://$ARTIFACTS" --force --region "$REGION" || true
echo "Main stack deleted. If you ran the OIDC bootstrap stack, delete it separately."
