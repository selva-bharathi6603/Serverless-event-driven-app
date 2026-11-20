"""SQS consumer: turns processing results into SNS email notifications."""
import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TOPIC_ARN = os.environ["TOPIC_ARN"]
_sns = boto3.client("sns")


def build_message(data):
    line_count = data.get("line_count")
    lines = [
        "A file was processed.",
        "",
        f"Object      : s3://{data.get('bucket')}/{data.get('object_key')}",
        f"Size        : {data.get('size_bytes')} bytes",
        f"Line count  : {line_count if line_count is not None else 'n/a (not a text file)'}",
        f"Processed at: {data.get('processed_at')}",
    ]
    return "\n".join(lines)


def handler(event, context):
    failures = []
    for record in event["Records"]:
        try:
            data = json.loads(record["body"])
            subject = f"File processed: {data['object_key']}"
            subject = subject.encode("ascii", "ignore").decode()[:100]
            _sns.publish(TopicArn=TOPIC_ARN, Subject=subject, Message=build_message(data))
        except Exception:
            logger.exception("failed to publish", extra={"message_id": record["messageId"]})
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
