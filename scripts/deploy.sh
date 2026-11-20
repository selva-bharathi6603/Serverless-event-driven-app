#!/usr/bin/env bash
# Manual deploy from your laptop / WSL. Usage: ALERT_EMAIL=you@example.com ./scripts/deploy.sh
set -euo pipefail

STACK_NAME="${STACK_NAME:-serverless-demo}"
REGION="${AWS_REGION:-ap-south-1}"
: "${ALERT_EMAIL:?Set ALERT_EMAIL to the address that should receive notifications}"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="${STACK_NAME}-artifacts-${ACCOUNT_ID}-${REGION}"

if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION"
fi

aws cloudformation package \
  --template-file template.yaml \
  --s3-bucket "$BUCKET" \
  --output-template-file packaged.yaml

aws cloudformation deploy \
  --template-file packaged.yaml \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides ProjectName="$STACK_NAME" AlertEmail="$ALERT_EMAIL"

aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query "Stacks[0].Outputs" --output table
