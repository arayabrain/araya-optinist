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


ECS_TASK_ID: str = _get_ecs_task_id()
ECS_SERVICE_NAME: str = _get_ecs_service_name()
