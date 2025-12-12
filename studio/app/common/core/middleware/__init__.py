"""
Middleware modules for the Studio application
"""

from studio.app.common.core.middleware.free_user_activity_middleware import (
    FreeUserActivityMiddleware,
)
from studio.app.common.core.middleware.logging_middleware import (
    ClientIdLoggingMiddleware,
)
from studio.app.common.core.middleware.spa_routing_middleware import (
    SPARoutingMiddleware,
)

__all__ = [
    "ClientIdLoggingMiddleware",
    "FreeUserActivityMiddleware",
    "SPARoutingMiddleware",
]
