"""Generates docs/architecture.png with AWS service icons.

Requirements: Graphviz installed on the machine, then `pip install diagrams`.
Run from the repository root:  python docs/architecture.py
"""
from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import EC2, Lambda
from diagrams.aws.database import Dynamodb
from diagrams.aws.integration import Eventbridge, SNS, SQS
from diagrams.aws.management import Cloudformation, Cloudwatch
from diagrams.aws.network import APIGateway
from diagrams.aws.security import IAM, SecretsManager
from diagrams.aws.storage import S3
from diagrams.onprem.ci import GithubActions
from diagrams.onprem.client import User, Users

graph_attr = {"fontsize": "22", "pad": "0.5", "nodesep": "0.7", "ranksep": "1.1", "splines": "spline"}

with Diagram(
    "Serverless Event-Driven App on AWS",
    filename="docs/architecture",
    outformat="png",
    show=False,
    direction="LR",
    graph_attr=graph_attr,
):
    client = Users("API client")
    uploader = User("File upload")
    inbox = User("Email inbox")

    with Cluster("CI/CD"):
        gha = GithubActions("GitHub Actions\n(lint, test, deploy)")
        oidc_role = IAM("Deploy role\n(OIDC, no keys)")
        cfn = Cloudformation("CloudFormation\n(provisions everything)")
        gha >> Edge(label="OIDC token") >> oidc_role >> cfn

    with Cluster("AWS Cloud (ap-south-1)"):
        secret = SecretsManager("Secrets Manager\n(API key, webhook)")
        topic = SNS("SNS topic")

        with Cluster("REST API"):
            api = APIGateway("HTTP API")
            api_fn = Lambda("api")
            items = Dynamodb("items table")
            api >> api_fn >> items

        with Cluster("File pipeline"):
            bucket = S3("uploads/ bucket")
            proc_fn = Lambda("processor")
            uploads = Dynamodb("uploads table")
            queue = SQS("results queue")
            dlq = SQS("dead-letter queue")
            notif_fn = Lambda("notifier")
            bucket >> proc_fn >> uploads
            proc_fn >> queue >> notif_fn
            queue >> Edge(style="dashed", label="3 failed receives") >> dlq

        with Cluster("Scheduled cleanup"):
            schedule = Eventbridge("EventBridge\ndaily schedule")
            clean_fn = Lambda("cleanup")
            ec2 = EC2("idle EC2, EBS,\nElastic IPs")
            schedule >> clean_fn >> Edge(style="dashed", label="report / stop") >> ec2

        with Cluster("Observability"):
            cw = Cloudwatch("Dashboard, alarms,\nJSON logs")

    client >> Edge(label="x-api-key") >> api
    api_fn >> Edge(style="dashed", label="read key") >> secret
    clean_fn >> Edge(style="dashed") >> secret
    uploader >> bucket
    notif_fn >> topic
    clean_fn >> topic
    cw >> Edge(label="alarms") >> topic
    topic >> inbox
