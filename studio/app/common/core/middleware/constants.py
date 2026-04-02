# Paths that should skip authentication-related middleware processing.
# These endpoints are either public (login, refresh) or internal (health check).
SKIP_AUTH_PATHS = ["/health", "/auth/login", "/auth/refresh"]

# Authenticated paths that should NOT update last_activity (automated/timer
# requests whose activity would prevent inactivity cleanup from working).
SKIP_ACTIVITY_PATHS = ["/log-report/frontend-errors"]
