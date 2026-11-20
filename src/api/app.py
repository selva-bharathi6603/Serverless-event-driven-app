"""HTTP API handler: CRUD for /items backed by DynamoDB.

Every route except GET /health needs an x-api-key header. The expected key
lives in AWS Secrets Manager and is cached in memory for five minutes.
"""
import hmac
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLE_NAME = os.environ["TABLE_NAME"]
SECRET_ARN = os.environ["SECRET_ARN"]
SECRET_TTL_SECONDS = 300

_table = boto3.resource("dynamodb").Table(TABLE_NAME)
_secrets = boto3.client("secretsmanager")
_cache = {"api_key": None, "fetched_at": 0.0}


def _json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    raise TypeError(f"Cannot serialise {type(value)}")


def response(status, body=None):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=_json_default) if body is not None else "",
    }


def get_api_key():
    now = time.time()
    if _cache["api_key"] is None or now - _cache["fetched_at"] > SECRET_TTL_SECONDS:
        secret = _secrets.get_secret_value(SecretId=SECRET_ARN)
        _cache["api_key"] = json.loads(secret["SecretString"])["api_key"]
        _cache["fetched_at"] = now
    return _cache["api_key"]


def is_authorized(event):
    provided = (event.get("headers") or {}).get("x-api-key", "")
    return bool(provided) and hmac.compare_digest(provided.encode(), get_api_key().encode())


def create_item(event):
    payload = json.loads(event.get("body") or "{}")
    name = payload.get("name")
    description = payload.get("description", "")
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise ValueError("'name' is required (1-100 characters)")
    if not isinstance(description, str) or len(description) > 500:
        raise ValueError("'description' must be a string up to 500 characters")
    item = {
        "id": str(uuid.uuid4()),
        "name": name.strip(),
        "description": description,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _table.put_item(Item=item)
    logger.info("item created", extra={"item_id": item["id"]})
    return response(201, item)


def list_items(event):
    result = _table.scan(Limit=50)
    return response(200, {"items": result.get("Items", [])})


def get_item(event):
    item_id = event["pathParameters"]["id"]
    item = _table.get_item(Key={"id": item_id}).get("Item")
    if item is None:
        return response(404, {"error": "item not found"})
    return response(200, item)


def delete_item(event):
    item_id = event["pathParameters"]["id"]
    try:
        _table.delete_item(Key={"id": item_id}, ConditionExpression="attribute_exists(id)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return response(404, {"error": "item not found"})
        raise
    logger.info("item deleted", extra={"item_id": item_id})
    return response(204)


ROUTES = {
    "POST /items": create_item,
    "GET /items": list_items,
    "GET /items/{id}": get_item,
    "DELETE /items/{id}": delete_item,
}


def handler(event, context):
    route = event.get("routeKey")
    try:
        if route == "GET /health":
            return response(200, {"status": "ok"})
        if not is_authorized(event):
            logger.warning("unauthorized request", extra={"route": route})
            return response(401, {"error": "unauthorized"})
        route_fn = ROUTES.get(route)
        if route_fn is None:
            return response(404, {"error": "route not found"})
        return route_fn(event)
    except ValueError as exc:
        return response(400, {"error": str(exc)})
    except ClientError:
        logger.exception("AWS call failed", extra={"route": route})
        return response(500, {"error": "internal error"})
