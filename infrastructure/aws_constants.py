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


class PremiumInstanceConfig:
    """
    Configuration constants for premium EC2 instances.

    These values are used for identifying and filtering premium instances
    in EC2, ECS, and ALB operations.
    """

    # Identifier used in EC2 instance Name/Tier/Type tags
    INSTANCE_IDENTIFIER = "premium"


class RoutingHeaders:
    """
    HTTP header names for ALB routing.

    These headers are used to route premium users to their dedicated instances.
    This is the single source of truth for routing header constants.
    Frontend equivalent: frontend/src/const/Subscription.ts (RoutingHeaders)
    """

    # Secure, non-reversible routing token (HMAC-SHA256)
    ROUTING_ID = "X-Routing-ID"
    # User subscription tier indicator
    USER_TIER = "X-User-Tier"


class DatabaseConfig:
    """
    Database connection configuration constants.

    Centralized database settings used across all Lambda packages.
    """

    # Default MySQL port (standard MySQL port)
    DEFAULT_PORT = 3306
