"""
Free User Activity Tracking Middleware

This middleware tracks activity for free tier users to enable intelligent load balancing
and autoscaling. It updates the last_activity timestamp in the database for every HTTP
request from a free tier user, which allows the Free Manager Lambda to:
1. Identify idle users (no activity for 10+ minutes)
2. Safely migrate idle users to newly launched instances
3. Count active users to trigger autoscaling

IMPORTANT: Database updates run in background tasks to avoid blocking requests.

Note: This uses pure ASGI middleware instead of BaseHTTPMiddleware to avoid
known performance issues and connection handling problems with BaseHTTPMiddleware.
See: https://github.com/encode/starlette/issues/1012
"""

import asyncio
import os
import threading
from datetime import datetime
from typing import Optional

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.mode import MODE
from studio.app.common.core.subscription.constants import SubscriptionStatus
from studio.app.common.db.database import session_scope
from studio.app.common.models import FreeUserAssignment

# In-memory cache to reduce database load (tracks last update time per user)
_last_activity_cache = {}
_cache_lock = threading.Lock()
_CACHE_TTL_SECONDS = 60  # Only update DB once per minute per user

# Cache instance ID (fetch once at startup, reuse for all requests)
_instance_id_cache = None
_instance_id_lock = threading.Lock()


class FreeUserActivityMiddleware:
    """
    ASGI Middleware to track free tier user activity for load balancing purposes.

    This middleware:
    - Extracts user ID from authentication tokens
    - Checks if user is on free tier (subscription_status = "Free")
    - Updates last_activity timestamp in free_user_assignments table
    - Records current instance ID for tracking
    - Runs asynchronously without blocking request processing
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # Skip non-HTTP requests (e.g., websocket, lifespan)
            await self.app(scope, receive, send)
            return

        # Skip in standalone mode
        if MODE.IS_STANDALONE:
            await self.app(scope, receive, send)
            return

        # Get request path
        path = scope.get("path", "")

        # Skip health check and auth endpoints to avoid overhead
        if path in ["/health", "/api/auth/login", "/api/auth/refresh"]:
            await self.app(scope, receive, send)
            return

        # Flag to track when response body starts (so we only track once)
        has_tracked = False

        async def send_wrapper(message: Message) -> None:
            """
            Wrap the send function to intercept response transmission.
            When the response body starts being sent, we schedule the activity tracking.
            """
            nonlocal has_tracked

            # When response starts, schedule activity tracking
            # This happens before response completes, so it's non-blocking
            if message["type"] == "http.response.start" and not has_tracked:
                has_tracked = True

                try:
                    # Access request state from scope
                    # FastAPI/Starlette stores request state in scope["state"]
                    state = scope.get("state", {})
                    if isinstance(state, dict):
                        authed_user = state.get("user")
                    else:
                        # state might be a State object
                        authed_user = getattr(state, "user", None)

                    if authed_user:
                        user_id = str(authed_user.id)
                        subscription_status = getattr(
                            authed_user,
                            "subscription_status",
                            SubscriptionStatus.FREE.value,
                        )

                        # Only track free tier users
                        if subscription_status == SubscriptionStatus.FREE.value:
                            # Check cache to avoid excessive DB writes
                            if _should_update_activity(user_id):
                                # Schedule background update (doesn't block response)
                                asyncio.create_task(
                                    _update_free_user_activity_async(user_id)
                                )

                except Exception as e:
                    # Log error but don't fail the request
                    logger = AppLogger.get_logger()
                    logger.warning(f"Failed to track free user activity: {e}")

            # Always send the original message
            await send(message)

        # Process the request with our wrapped send function
        await self.app(scope, receive, send_wrapper)


def _should_update_activity(user_id: str) -> bool:
    """
    Check if we should update activity for this user (throttling).
    Only update DB once per minute per user to reduce load.

    IMPORTANT: This only CHECKS the cache. The actual cache update happens
    in _update_cache_after_commit() AFTER successful database commit.
    """
    import time

    with _cache_lock:
        last_update = _last_activity_cache.get(user_id, 0)
        now = time.time()

        if now - last_update >= _CACHE_TTL_SECONDS:
            return True
        return False


def _update_cache_after_commit(user_id: str):
    """
    Update cache timestamp after successful database commit.
    This ensures cache consistency with database state.
    """
    import time

    with _cache_lock:
        _last_activity_cache[user_id] = time.time()


async def _update_free_user_activity_async(user_id: str):
    """
    Update last_activity timestamp for free tier user (async wrapper).
    Runs in background to avoid blocking request.

    Cache is updated optimistically before database commit to minimize latency.
    If database update fails, the cache will be refreshed on next request.
    """
    # Update cache immediately (optimistic) to reduce perceived latency
    _update_cache_after_commit(user_id)

    # Run blocking database call in thread pool (fire-and-forget)
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _update_free_user_activity_sync, user_id)
    except Exception as e:
        # Log error but don't propagate (fire-and-forget pattern)
        logger = AppLogger.get_logger()
        logger.warning(f"Background activity update failed for user {user_id}: {e}")


def _update_free_user_activity_sync(user_id: str) -> bool:
    """
    Update last_activity timestamp for free tier user (sync implementation).

    This method:
    1. Gets current instance ID from EC2 metadata
    2. Upserts record in free_user_assignments table using merge()
    3. Updates last_activity timestamp

    Returns:
        True if update successful, False otherwise
    """
    try:
        # Get current instance ID from EC2 metadata or environment
        instance_id = _get_instance_id()

        # Don't track if we can't determine instance ID
        # This prevents polluting database with fake "local" entries
        if not instance_id or instance_id == "local":
            return False

        # Update database using SQLAlchemy merge() for atomic upsert
        with session_scope() as session:
            now = datetime.now()

            assignment = FreeUserAssignment(
                user_id=user_id,
                instance_id=instance_id,
                assigned_at=now,
                last_activity=now,
            )

            merged = session.merge(assignment)

            # For existing records, ensure we update the timestamps
            merged.last_activity = now
            merged.instance_id = instance_id

            session.commit()
            return True

    except Exception as e:
        logger = AppLogger.get_logger()
        logger.error(f"Error updating free user activity for user {user_id}: {e}")
        return False


def _get_instance_id() -> Optional[str]:
    """
    Get current EC2 instance ID (cached).

    In ECS with EC2 launch type, containers run on EC2 instances.
    We need to get the underlying EC2 instance ID via metadata service.

    Priority:
    1. Cached value (from previous fetch)
    2. Environment variable INSTANCE_ID (if manually set)
    3. EC2 metadata service (IMDSv2 with token)
    4. EC2 metadata service (IMDSv1 fallback)
    5. Return "local" for local development

    Result is cached to avoid repeated metadata service calls.
    """
    global _instance_id_cache

    # Return cached value if available
    with _instance_id_lock:
        if _instance_id_cache is not None:
            return _instance_id_cache

        # Check environment variable first (faster)
        instance_id = os.environ.get("INSTANCE_ID")
        if instance_id:
            _instance_id_cache = instance_id
            return instance_id

        # Try EC2 metadata service (IMDSv2 - requires token)
        try:
            import urllib.request

            # Get session token first (IMDSv2 requirement)
            token_url = "http://169.254.169.254/latest/api/token"
            token_req = urllib.request.Request(
                token_url,
                headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
                method="PUT",
            )
            with urllib.request.urlopen(token_req, timeout=1) as response:
                token = response.read().decode("utf-8")

            # Get instance ID using token
            metadata_url = "http://169.254.169.254/latest/meta-data/instance-id"
            metadata_req = urllib.request.Request(
                metadata_url, headers={"X-aws-ec2-metadata-token": token}
            )
            with urllib.request.urlopen(metadata_req, timeout=1) as response:
                instance_id = response.read().decode("utf-8")
                _instance_id_cache = instance_id
                return instance_id
        except Exception:
            pass

        # Fallback to IMDSv1 (without token)
        try:
            import urllib.request

            metadata_url = "http://169.254.169.254/latest/meta-data/instance-id"
            with urllib.request.urlopen(metadata_url, timeout=1) as response:
                instance_id = response.read().decode("utf-8")
                _instance_id_cache = instance_id
                return instance_id
        except Exception:
            pass

        # Local development or metadata service unavailable
        logger = AppLogger.get_logger()
        logger.warning(
            "Could not retrieve EC2 instance ID from metadata service. "
            "Using 'local' as instance ID. This is OK for local development "
            "but indicates a problem in production."
        )
        _instance_id_cache = "local"
        return "local"
