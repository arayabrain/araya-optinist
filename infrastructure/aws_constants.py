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


class EnvironmentConfig:
    """
    Deployment environment constants.

    Provides the environment prefix (read from the ENV_PREFIX env var)
    and a validated human-readable label for AWS resource tagging.
    Must match Terraform var.environment / local.environment_label.
    """

    # Environment variable name for the deployment prefix
    ENV_PREFIX_VAR = "ENV_PREFIX"
    # Known environment prefix values (must match Terraform var.environment)
    PRODUCTION = "subscr"
    DEVELOPMENT = "development"

    # Map of known prefixes to human-readable labels.
    # Matches the Terraform local.environment_label convention.
    _LABELS = {
        PRODUCTION: "Production",
        DEVELOPMENT: "Development",
    }

    @classmethod
    def get_env_prefix(cls) -> str:
        """Get the environment prefix from ENV_PREFIX env var.

        Raises ValueError if ENV_PREFIX is not set, to prevent a misconfigured
        Lambda from silently operating on the wrong environment's instances.
        """
        import os

        prefix = os.environ.get(cls.ENV_PREFIX_VAR)
        if not prefix:
            raise ValueError(
                f"{cls.ENV_PREFIX_VAR} environment variable is not set. "
                "Refusing to proceed without an explicit environment prefix "
                "to prevent cross-environment contamination."
            )
        return prefix

    @classmethod
    def get_environment_label(cls) -> str:
        """Get the human-readable environment label for AWS tags.

        Raises ValueError if the prefix is not a recognised environment name,
        to prevent silent mis-tagging of resources.
        """
        prefix = cls.get_env_prefix()
        label = cls._LABELS.get(prefix)
        if label is None:
            raise ValueError(
                f"Unknown environment prefix '{prefix}'. "
                f"Expected one of: {sorted(cls._LABELS)}"
            )
        return label


class PremiumInstanceConfig:
    """
    Configuration constants for premium EC2 instances.

    These values are used for identifying and filtering premium instances
    in EC2, ECS, and ALB operations.
    """

    # Identifier used in EC2 instance Name/Tier/Type tags
    INSTANCE_IDENTIFIER = "premium"
    # EC2 Type tag value for premium instances
    INSTANCE_TYPE_TAG = "Premium-Instance"
    # EC2 Service tag value for premium instances
    SERVICE_TAG = "premium-tier"
    # Instance Name tag suffix (combined with env prefix: "{prefix}-premium-running")
    INSTANCE_NAME_SUFFIX = "premium-running"

    @classmethod
    def get_env_prefix(cls) -> str:
        """Delegate to EnvironmentConfig.get_env_prefix for convenience."""
        return EnvironmentConfig.get_env_prefix()

    @classmethod
    def get_instance_name_pattern(cls) -> str:
        """Get the EC2 Name tag wildcard pattern for this environment.

        Returns e.g. 'development-premium-*' or 'subscr-premium-*'.
        Used in AWS API tag:Name filters.
        """
        return f"{EnvironmentConfig.get_env_prefix()}-{cls.INSTANCE_IDENTIFIER}-*"

    @classmethod
    def get_instance_name(cls) -> str:
        """Get the EC2 Name tag value for new instances.

        Returns e.g. 'development-premium-running' or 'subscr-premium-running'.
        """
        return f"{EnvironmentConfig.get_env_prefix()}-{cls.INSTANCE_NAME_SUFFIX}"


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
    # IMPORTANT: Shares the TERMINATING DB value to avoid a schema migration.
    # Safe only because all status checks use enum constants, not raw strings.
    # Adding code that matches TERMINATING will also match PENDING_RELEASE rows.
    # If a distinct status is ever needed, migrate the column value first.
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

    # Handler action values (API request body "action" field)
    ACTION_ASSIGN = "assign"
    ACTION_RELEASE = "release"
    ACTION_UPDATE_ACTIVITY = "update_activity"


class DatabaseConfig:
    """
    Database connection configuration constants.

    Centralized database settings used across all Lambda packages.
    """

    # Default MySQL port (standard MySQL port)
    DEFAULT_PORT = 3306
