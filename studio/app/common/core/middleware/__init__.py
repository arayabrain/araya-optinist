"""
Middleware modules for the Studio application
"""

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
