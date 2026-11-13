# Paths that should skip authentication-related middleware processing.
# These endpoints are either public (login, refresh) or internal (health check).
SKIP_AUTH_PATHS = ["/health", "/auth/login", "/auth/refresh", "/auth/logout"]

# Placeholder routing HMAC key for local development. Cloud deployments set
# ROUTING_SECRET_KEY; startup refuses to run there on this value
# (studio/app/common/core/auth/secret_validation.py).
DEFAULT_ROUTING_SECRET_KEY = "dev-key-not-for-production"

# Authenticated paths that should NOT update last_activity (automated/timer
# requests whose activity would prevent inactivity cleanup from working).
SKIP_ACTIVITY_PATHS = ["/log-report/frontend-errors"]
