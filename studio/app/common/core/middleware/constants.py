# Paths that should skip authentication-related middleware processing.
# These endpoints are either public (login, refresh) or internal (health check).
SKIP_AUTH_PATHS = ["/health", "/auth/login", "/auth/refresh"]
