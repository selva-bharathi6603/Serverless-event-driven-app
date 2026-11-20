"""Scheduled AWS hygiene report.

* Idle EC2: running instances tagged AutoStop=true with low average CPU are
  stopped (only when DRY_RUN=false), otherwise just reported.
* Unattached EBS volumes (status "available") and unassociated Elastic IPs are
  only flagged, never deleted.
"""
import json
import logging
import os
import urllib.request
from datetime import datetime, timedelta, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TOPIC_ARN = os.environ["TOPIC_ARN"]
SECRET_ARN = os.environ["SECRET_ARN"]
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
CPU_THRESHOLD = float(os.environ.get("CPU_THRESHOLD", "5"))
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "24"))

_ec2 = boto3.client("ec2")
_cloudwatch = boto3.client("cloudwatch")
_sns = boto3.client("sns")
_secrets = boto3.client("secretsmanager")


def average_cpu(instance_id):
    end = datetime.now(timezone.utc)
    stats = _cloudwatch.get_metric_statistics(
        Namespace="AWS/EC2",
        MetricName="CPUUtilization",
        Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
        StartTime=end - timedelta(hours=LOOKBACK_HOURS),
        EndTime=end,
        Period=3600,
        Statistics=["Average"],
    )
    points = [p["Average"] for p in stats["Datapoints"]]
    return sum(points) / len(points) if points else None


def find_idle_instances():
    idle = []
    paginator = _ec2.get_paginator("describe_instances")
    filters = [
        {"Name": "instance-state-name", "Values": ["running"]},
        {"Name": "tag:AutoStop", "Values": ["true"]},
    ]
    for page in paginator.paginate(Filters=filters):
        for reservation in page["Reservations"]:
            for instance in reservation["Instances"]:
                cpu = average_cpu(instance["InstanceId"])
                if cpu is not None and cpu < CPU_THRESHOLD:
                    idle.append({"id": instance["InstanceId"], "avg_cpu": round(cpu, 2)})
    return idle


def find_unattached_volumes():
    paginator = _ec2.get_paginator("describe_volumes")
    volumes = []
    for page in paginator.paginate(Filters=[{"Name": "status", "Values": ["available"]}]):
        for vol in page["Volumes"]:
            volumes.append({"id": vol["VolumeId"], "size_gb": vol["Size"], "created": vol["CreateTime"].isoformat()})
    return volumes


def find_unused_addresses():
    addresses = _ec2.describe_addresses()["Addresses"]
    return [{"ip": a["PublicIp"], "allocation_id": a.get("AllocationId")} for a in addresses if "AssociationId" not in a]


def build_report(idle, stopped, volumes, addresses):
    mode = "DRY RUN (nothing was stopped)" if DRY_RUN else "LIVE"
    lines = [f"AWS cleanup report - mode: {mode}", ""]
    lines.append(f"Idle EC2 instances (avg CPU < {CPU_THRESHOLD}% over {LOOKBACK_HOURS}h, tag AutoStop=true): {len(idle)}")
    lines += [f"  - {i['id']} avg CPU {i['avg_cpu']}%" + (" -> STOPPED" if i["id"] in stopped else "") for i in idle]
    lines.append(f"Unattached EBS volumes: {len(volumes)}")
    lines += [f"  - {v['id']} {v['size_gb']} GiB, created {v['created']}" for v in volumes]
    lines.append(f"Unassociated Elastic IPs: {len(addresses)}")
    lines += [f"  - {a['ip']} ({a['allocation_id']})" for a in addresses]
    return "\n".join(lines)


def send_webhook(text):
    """Post the report to the webhook URL kept in Secrets Manager, if one is set."""
    try:
        secret = json.loads(_secrets.get_secret_value(SecretId=SECRET_ARN)["SecretString"])
        url = secret.get("webhook_url", "")
        if not url.startswith("https://"):
            return False
        request = urllib.request.Request(
            url, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=5) as resp:  # https only
            return resp.status < 300
    except Exception:
        logger.exception("webhook delivery failed")
        return False


def handler(event, context):
    idle = find_idle_instances()
    stopped = []
    if idle and not DRY_RUN:
        ids = [i["id"] for i in idle]
        _ec2.stop_instances(InstanceIds=ids)
        stopped = ids
    volumes = find_unattached_volumes()
    addresses = find_unused_addresses()
    report = build_report(idle, stopped, volumes, addresses)
    _sns.publish(TopicArn=TOPIC_ARN, Subject="AWS cleanup report", Message=report)
    webhook_sent = send_webhook(report)
    summary = {
        "idle_instances": len(idle),
        "stopped": len(stopped),
        "unattached_volumes": len(volumes),
        "unused_addresses": len(addresses),
        "dry_run": DRY_RUN,
        "webhook_sent": webhook_sent,
    }
    logger.info("cleanup finished", extra=summary)
    return summary
