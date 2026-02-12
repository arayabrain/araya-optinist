"""
Middleware modules for the Studio application
"""

from studio.app.common.core.middleware.logging_middleware import (
    ClientIdLoggingMiddleware,
)
from studio.app.common.core.middleware.secure_routing_middleware import (
    SecureRoutingMiddleware,
)
from studio.app.common.core.middleware.spa_routing_middleware import (
    SPARoutingMiddleware,
)
from studio.app.common.core.middleware.user_activity_middleware import (
    FreeUserActivityMiddleware,  # Backwards compatibility alias
)
from studio.app.common.core.middleware.user_activity_middleware import (
    UserActivityMiddleware,
)

__all__ = [
    "ClientIdLoggingMiddleware",
    "FreeUserActivityMiddleware",  # Backwards compatibility alias
    "SecureRoutingMiddleware",
    "SPARoutingMiddleware",
    "UserActivityMiddleware",
]
