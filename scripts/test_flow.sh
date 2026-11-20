#!/usr/bin/env bash
# End-to-end check against the deployed stack.
set -euo pipefail

STACK_NAME="${STACK_NAME:-serverless-demo}"
REGION="${AWS_REGION:-ap-south-1}"

output() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

API_URL=$(output ApiUrl)
BUCKET=$(output UploadBucketName)
SECRET_ARN=$(output SecretArn)
API_KEY=$(aws secretsmanager get-secret-value --secret-id "$SECRET_ARN" --region "$REGION" \
  --query SecretString --output text | python3 -c 'import sys, json; print(json.load(sys.stdin)["api_key"])')

echo "== health (no key needed)"
curl -s "$API_URL/health"; echo

echo "== request without key (expect 401)"
curl -s -o /dev/null -w "%{http_code}\n" "$API_URL/items"

echo "== create item"
curl -s -X POST "$API_URL/items" -H "x-api-key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"name":"first item","description":"created by test_flow.sh"}'; echo

echo "== list items"
curl -s "$API_URL/items" -H "x-api-key: $API_KEY"; echo

echo "== upload a file to trigger the S3 -> Lambda -> SQS -> SNS pipeline"
printf 'line one\nline two\nline three\n' > /tmp/sample.txt
aws s3 cp /tmp/sample.txt "s3://$BUCKET/uploads/sample-$(date +%s).txt" --region "$REGION"
echo "Check your inbox for the 'File processed' email (can take up to a minute)."

echo "== run the cleanup Lambda now (dry run unless you changed CleanupDryRun)"
FUNCTION=$(aws cloudformation describe-stack-resource --stack-name "$STACK_NAME" --region "$REGION" \
  --logical-resource-id CleanupFunction --query StackResourceDetail.PhysicalResourceId --output text)
aws lambda invoke --function-name "$FUNCTION" --region "$REGION" /tmp/cleanup-out.json >/dev/null
cat /tmp/cleanup-out.json; echo
