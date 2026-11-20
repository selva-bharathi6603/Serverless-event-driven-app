"""Triggered by S3 uploads under uploads/. Records file stats and queues a message."""
import json
import logging
import os
import urllib.parse
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

QUEUE_URL = os.environ["QUEUE_URL"]
MAX_READ_BYTES = 1_000_000
TEXT_EXTENSIONS = (".csv", ".txt", ".json", ".log")

_s3 = boto3.client("s3")
_sqs = boto3.client("sqs")
_table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def process_object(bucket, key):
    head = _s3.head_object(Bucket=bucket, Key=key)
    size = head["ContentLength"]
    line_count = None
    if key.lower().endswith(TEXT_EXTENSIONS) and size <= MAX_READ_BYTES:
        body = _s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        line_count = len(body.splitlines())
    result = {
        "object_key": key,
        "bucket": bucket,
        "size_bytes": size,
        "line_count": line_count,
        "content_type": head.get("ContentType", "unknown"),
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
    _table.put_item(Item=result)
    _sqs.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(result))
    logger.info("file processed", extra={"key": key, "size_bytes": size, "line_count": line_count})
    return result


def handler(event, context):
    results = []
    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        try:
            results.append(process_object(bucket, key))
        except Exception:
            logger.exception("failed to process object", extra={"key": key})
            raise
    return {"processed": len(results)}
