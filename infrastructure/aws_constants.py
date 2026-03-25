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


class InstanceState:
    """
    Instance state constants for premium EC2 instances.

    Includes both AWS EC2 states (returned by describe_instances API)
    and DB-tracked states used in the premium_user_assignments table.

    AWS EC2 states: pending, running, stopping, stopped, shutting-down, terminated
    DB-tracked states: starting, launching (transitional states tracked in DB)
    """

    # AWS EC2 states
    PENDING = "pending"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    SHUTTING_DOWN = "shutting-down"
    TERMINATED = "terminated"

    # DB-tracked transitional states (not AWS states)
    STARTING = "starting"
    LAUNCHING = "launching"


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


class PremiumAssignment:
    """
    Premium user assignment constants.

    These values are used in the premium_user_assignments table
    to track assignment states and special markers.

    Status values:
        ACTIVE, MIGRATING, TERMINATING - lifecycle states

    Marker values:
        STANDBY, RESERVING - placeholder values for special entries
    """

    # Status: Active assignment - user is assigned and can access the instance
    ACTIVE = "active"
    # Status: Assignment is being migrated to a new instance
    MIGRATING = "migrating"
    # Status: Assignment is being terminated
    TERMINATING = "terminating"
    # Status: Soft-released via beacon (grace period before full teardown).
    # Reused TERMINATING enum value to avoid DB migration.
    PENDING_RELEASE = "terminating"
    # Grace period (seconds) before a pending_release is finalized.
    PENDING_RELEASE_GRACE_SECONDS = 120

    # Marker: Standby pool entries (no real ALB rule/target group yet)
    STANDBY = "standby"
    # Marker: Instances being reserved for a user
    RESERVING = "reserving"
    # Marker: User temporarily assigned to shared autoscaling pool
    # (awaiting migration to dedicated instance)
    AUTOSCALING_POOL = "autoscaling-pool"


class DatabaseConfig:
    """
    Database connection configuration constants.

    Centralized database settings used across all Lambda packages.
    """

    # Default MySQL port (standard MySQL port)
    DEFAULT_PORT = 3306
