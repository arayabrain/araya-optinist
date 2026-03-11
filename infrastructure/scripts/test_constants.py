"""Shared test constants for infrastructure test suites."""

# Base mock environment variables shared across premium test files
MOCK_ENV_VARS_BASE = {
    "RDS_HOST": "test-db.example.com:3306",
    "RDS_USER": "test_user",
    "RDS_PASSWORD": "test_pass",
    "RDS_DATABASE": "test_db",
    "VPC_ID": "vpc-test123",
    "ALB_LISTENER_ARN": ("arn:aws:elasticloadbalancing:region:account:listener/test"),
    "ROUTING_SECRET_KEY": "test-secret-key-12345",
}

# Extended mock env vars for tests requiring autoscaling/cluster config
MOCK_ENV_VARS_PREMIUM = {
    **MOCK_ENV_VARS_BASE,
    "AUTOSCALING_TARGET_GROUP_ARN": (
        "arn:aws:elasticloadbalancing:region:account:targetgroup/asg"
    ),
    "CLUSTER_NAME": "test-cluster",
    "PREMIUM_SERVICE_NAME": "subscr-optinist-premium-service",
    "PREMIUM_INSTANCE_IDS": "i-test1,i-test2,i-test3",
    "PREMIUM_STANDBY_POOL_SIZE": "2",
    "PREMIUM_IDLE_TIMEOUT_HOURS": "3",
    "PREMIUM_EXTRA_CAPACITY": "1",
    "ABSOLUTE_MAX": "10",
}


class MockRow:
    """Mock database row that supports both dict and index access."""

    def __init__(self, data):
        self.data = data

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.data.values())[key]
        return self.data.get(key)

    def get(self, key, default=None):
        return self.data.get(key, default)
