"""Platform / infrastructure metadata retrieved from the ECS container
metadata endpoint (v4).

The public module-level constants ``ECS_TASK_ID`` and ``ECS_SERVICE_NAME``
are resolved once at process startup and may be imported by any module
that needs to identify the running platform context (logging, API
responses, etc.).
"""

import json
import os
import urllib.request

_ECS_METADATA_TIMEOUT = 2

NO_ECS_TASK_DEFAULT = "local"
NO_ECS_SERVICE_DEFAULT = "none"
NO_ECS_INSTANCE_DEFAULT = "none"


def _get_ecs_task_id() -> str:
    """Fetch the short ECS task ID from the container metadata
    endpoint (v4). Returns a default value outside ECS."""
    meta_uri = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
    if not meta_uri:
        return NO_ECS_TASK_DEFAULT
    try:
        req = urllib.request.Request(f"{meta_uri}/task")
        with urllib.request.urlopen(req, timeout=_ECS_METADATA_TIMEOUT) as resp:
            data = json.loads(resp.read())
        # TaskARN: arn:aws:ecs:region:account:task/cluster/id
        task_arn = data.get("TaskARN", "")
        return task_arn.rsplit("/", 1)[-1] if "/" in task_arn else NO_ECS_TASK_DEFAULT
    except Exception:
        return NO_ECS_TASK_DEFAULT


def _get_ecs_service_name() -> str:
    """Fetch the ECS service name from the container metadata
    endpoint (v4). Returns a default value if not available."""
    meta_uri = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
    if not meta_uri:
        return NO_ECS_SERVICE_DEFAULT
    try:
        req = urllib.request.Request(f"{meta_uri}/task")
        with urllib.request.urlopen(req, timeout=_ECS_METADATA_TIMEOUT) as resp:
            data = json.loads(resp.read())
        # ServiceName is only available for tasks started by a service
        service_name = data.get("ServiceName", "")
        return service_name if service_name else NO_ECS_SERVICE_DEFAULT
    except Exception:
        return NO_ECS_SERVICE_DEFAULT


def _get_ecs_instance_id() -> str:
    """Resolve the EC2 instance ID backing the current ECS task.

    Uses the ECS metadata endpoint to obtain the cluster and task ARN,
    then calls the ECS API (DescribeTasks → DescribeContainerInstances)
    to look up the underlying EC2 instance ID.
    Returns ``NO_ECS_INSTANCE_DEFAULT`` when the information is unavailable
    (e.g. Fargate, local development, or insufficient IAM permissions).
    """
    meta_uri = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
    if not meta_uri:
        return NO_ECS_INSTANCE_DEFAULT
    try:
        req = urllib.request.Request(f"{meta_uri}/task")
        with urllib.request.urlopen(req, timeout=_ECS_METADATA_TIMEOUT) as resp:
            data = json.loads(resp.read())

        task_arn = data.get("TaskARN", "")
        cluster_arn = data.get("Cluster", "")
        if not task_arn or not cluster_arn:
            return NO_ECS_INSTANCE_DEFAULT

        import boto3

        ecs = boto3.client("ecs")
        tasks_resp = ecs.describe_tasks(cluster=cluster_arn, tasks=[task_arn])
        tasks = tasks_resp.get("tasks", [])
        if not tasks:
            return NO_ECS_INSTANCE_DEFAULT

        container_instance_arn = tasks[0].get("containerInstanceArn", "")
        if not container_instance_arn:
            return NO_ECS_INSTANCE_DEFAULT

        ci_resp = ecs.describe_container_instances(
            cluster=cluster_arn, containerInstances=[container_instance_arn]
        )
        instances = ci_resp.get("containerInstances", [])
        if not instances:
            return NO_ECS_INSTANCE_DEFAULT

        return instances[0].get("ec2InstanceId", NO_ECS_INSTANCE_DEFAULT)
    except Exception:
        return NO_ECS_INSTANCE_DEFAULT


def _mask_instance_id(instance_id: str) -> str:
    """Truncate an EC2 instance ID to a short prefix for safe exposure.

    Example: ``i-0a1b2c3d4e5f67890`` → ``i-0a1b2c``

    The ``i-`` prefix and the first 6 hex characters are preserved so
    the value remains useful for prefix-searching in AWS Console or log files.
    """
    _VISIBLE_CHARS = 6
    prefix, _, hex_part = instance_id.partition("-")
    if not hex_part or len(hex_part) <= _VISIBLE_CHARS:
        return instance_id
    return f"{prefix}-{hex_part[:_VISIBLE_CHARS]}"


ECS_TASK_ID: str = _get_ecs_task_id()
ECS_SERVICE_NAME: str = _get_ecs_service_name()
ECS_INSTANCE_ID: str = _mask_instance_id(_get_ecs_instance_id())
