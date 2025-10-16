"""
Logging middleware for capturing user context in logs
"""

from typing import Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from studio.app.common.core.auth.auth_helper import extract_uid_from_request
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.mode import MODE


class ClientIdLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware to extract client_id from request and set it in logging context

    This middleware:
    - Extracts uid from authentication tokens (Firebase or JWT (ExToken))
    - Generate client_id from uid (by hashing, etc.)
    - Sets the client_id in the logging context for all log messages during the request
    - In standalone mode, skips client_id extraction and logging (client_id is blank)
    """

    async def dispatch(self, request: Request, call_next):
        client_id: Optional[str] = None

        # Skip uid extraction for standalone mode
        if not MODE.IS_STANDALONE:
            uid = extract_uid_from_request(request)
            client_id = AppLogger.generate_client_id(uid)

        # Set client_id in logging context
        AppLogger.set_client_id(client_id)

        # Process the request
        response = await call_next(request)

        return response
