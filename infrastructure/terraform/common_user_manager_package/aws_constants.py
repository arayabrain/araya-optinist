"""
AWS service status constants.

This module defines constants for AWS service statuses to ensure consistency
across the codebase and prevent typos.
"""


class ECSTaskStatus:
    """
    ECS Task status constants.

    These values are returned by AWS ECS APIs in the
    'lastStatus' and 'desiredStatus' fields.
    Reference: https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_Task.html
    """

    PROVISIONING = "PROVISIONING"
    PENDING = "PENDING"
    ACTIVATING = "ACTIVATING"
    RUNNING = "RUNNING"
    DEACTIVATING = "DEACTIVATING"
    STOPPING = "STOPPING"
    DEPROVISIONING = "DEPROVISIONING"
    STOPPED = "STOPPED"


class BatchJobStatus:
    """
    AWS Batch job status constants.

    These values are used when querying AWS Batch job statuses.
    Reference: https://docs.aws.amazon.com/batch/latest/userguide/job_states.html
    """

    SUBMITTED = "SUBMITTED"
    PENDING = "PENDING"
    RUNNABLE = "RUNNABLE"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class SubscriptionType:
    FREE = "free"
    PREMIUM = "premium"
