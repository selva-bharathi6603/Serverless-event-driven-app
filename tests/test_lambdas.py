import importlib.util
import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

ROOT = Path(__file__).resolve().parent.parent
REGION = "ap-south-1"
API_KEY = "test-key-123"


def load(name, relative_path):
    """Load a Lambda module by file path (all four are called app.py)."""
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")


def make_event(route, body=None, key=API_KEY, path_id=None):
    return {
        "routeKey": route,
        "headers": {"x-api-key": key} if key else {},
        "pathParameters": {"id": path_id} if path_id else None,
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {"http": {"method": route.split()[0]}},
    }


@pytest.fixture
def api(monkeypatch):
    with mock_aws():
        boto3.client("dynamodb").create_table(
            TableName="items",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        arn = boto3.client("secretsmanager").create_secret(
            Name="app", SecretString=json.dumps({"api_key": API_KEY})
        )["ARN"]
        monkeypatch.setenv("TABLE_NAME", "items")
        monkeypatch.setenv("SECRET_ARN", arn)
        yield load("api_app", "src/api/app.py")


def test_health_needs_no_key(api):
    result = api.handler(make_event("GET /health", key=None), None)
    assert result["statusCode"] == 200


def test_missing_or_wrong_key_is_rejected(api):
    assert api.handler(make_event("GET /items", key=None), None)["statusCode"] == 401
    assert api.handler(make_event("GET /items", key="wrong"), None)["statusCode"] == 401


def test_item_crud_flow(api):
    created = api.handler(make_event("POST /items", {"name": "first", "description": "demo"}), None)
    assert created["statusCode"] == 201
    item_id = json.loads(created["body"])["id"]

    fetched = api.handler(make_event("GET /items/{id}", path_id=item_id), None)
    assert json.loads(fetched["body"])["name"] == "first"

    listed = api.handler(make_event("GET /items"), None)
    assert len(json.loads(listed["body"])["items"]) == 1

    assert api.handler(make_event("DELETE /items/{id}", path_id=item_id), None)["statusCode"] == 204
    assert api.handler(make_event("GET /items/{id}", path_id=item_id), None)["statusCode"] == 404
    assert api.handler(make_event("DELETE /items/{id}", path_id=item_id), None)["statusCode"] == 404


@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": 5}, {"name": "x" * 101}])
def test_validation_errors(api, body):
    assert api.handler(make_event("POST /items", body), None)["statusCode"] == 400


def test_processor_stores_result_and_queues_message(monkeypatch):
    with mock_aws():
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket="uploads-bucket", CreateBucketConfiguration={"LocationConstraint": REGION})
        s3.put_object(Bucket="uploads-bucket", Key="uploads/data.csv", Body=b"a,b\n1,2\n3,4\n")
        queue_url = boto3.client("sqs").create_queue(QueueName="results")["QueueUrl"]
        boto3.client("dynamodb").create_table(
            TableName="uploads",
            KeySchema=[{"AttributeName": "object_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "object_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        monkeypatch.setenv("TABLE_NAME", "uploads")
        monkeypatch.setenv("QUEUE_URL", queue_url)
        processor = load("processor_app", "src/processor/app.py")

        event = {"Records": [{"s3": {"bucket": {"name": "uploads-bucket"}, "object": {"key": "uploads/data.csv"}}}]}
        assert processor.handler(event, None) == {"processed": 1}

        stored = boto3.resource("dynamodb").Table("uploads").get_item(Key={"object_key": "uploads/data.csv"})["Item"]
        assert stored["line_count"] == 3
        messages = boto3.client("sqs").receive_message(QueueUrl=queue_url)["Messages"]
        assert json.loads(messages[0]["Body"])["object_key"] == "uploads/data.csv"


def test_notifier_publishes_and_reports_failures(monkeypatch):
    with mock_aws():
        topic_arn = boto3.client("sns").create_topic(Name="alerts")["TopicArn"]
        monkeypatch.setenv("TOPIC_ARN", topic_arn)
        notifier = load("notifier_app", "src/notifier/app.py")
        good = {"messageId": "1", "body": json.dumps({"object_key": "uploads/a.txt", "bucket": "b", "size_bytes": 3})}
        bad = {"messageId": "2", "body": "not json"}
        result = notifier.handler({"Records": [good, bad]}, None)
        assert result == {"batchItemFailures": [{"itemIdentifier": "2"}]}


def test_cleanup_reports_unattached_volume_and_unused_ip(monkeypatch):
    with mock_aws():
        topic_arn = boto3.client("sns").create_topic(Name="alerts")["TopicArn"]
        arn = boto3.client("secretsmanager").create_secret(
            Name="app", SecretString=json.dumps({"api_key": "k", "webhook_url": ""})
        )["ARN"]
        ec2 = boto3.client("ec2")
        ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=8)
        ec2.allocate_address(Domain="vpc")
        monkeypatch.setenv("TOPIC_ARN", topic_arn)
        monkeypatch.setenv("SECRET_ARN", arn)
        cleanup = load("cleanup_app", "src/cleanup/app.py")
        summary = cleanup.handler({}, None)
        assert summary["unattached_volumes"] == 1
        assert summary["unused_addresses"] == 1
        assert summary["stopped"] == 0
        assert summary["dry_run"] is True
