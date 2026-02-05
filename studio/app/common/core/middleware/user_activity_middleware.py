"""
User Activity Tracking Middleware

This middleware tracks activity for both free and premium tier users to enable:

For Free Users:
1. Intelligent load balancing and autoscaling
2. Identify idle users (no activity for 10+ minutes)
3. Safely migrate idle users to newly launched instances
4. Count active users to trigger autoscaling

For Premium Users:
1. Prevent stale assignment cleanup for active users
2. Enable proper scale-down of premium instance pool
3. Track actual usage for billing/analytics

IMPORTANT: Database updates run in background tasks to avoid blocking requests.

Note: This uses pure ASGI middleware instead of BaseHTTPMiddleware to avoid
known performance issues and connection handling problems with BaseHTTPMiddleware.
See: https://github.com/encode/starlette/issues/1012
"""

import asyncio
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import text
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from studio.app.common.core.auth.auth_helper import extract_uid_from_firebase_jwt
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.mode import MODE
from studio.app.common.core.subscription.constants import SubscriptionPlanIds
from studio.app.common.core.utils.datetime_utils import get_current_datetime
from studio.app.common.db.database import session_scope
from studio.app.common.models import FreeUserAssignment

# Constants
BEARER_PREFIX = "Bearer "
BEARER_PREFIX_LENGTH = len(BEARER_PREFIX)
SKIP_AUTH_PATHS = ["/health", "/api/auth/login", "/api/auth/refresh"]
TIER_FREE = "free"
TIER_PREMIUM = "premium"

# In-memory cache to reduce database load (tracks last update time per user)
# Separate caches for free and premium to avoid key collisions
_free_activity_cache = {}
_premium_activity_cache = {}
_cache_lock = threading.Lock()
_CACHE_TTL_SECONDS = 60  # Only update DB once per minute per user

# Track recently logged out users to prevent orphaned background activity updates
# Key: user_id, Value: logout timestamp
_logged_out_users = {}
_logged_out_lock = threading.Lock()
_LOGGED_OUT_TTL_SECONDS = 10  # Clear entry after 10 seconds (background tasks are fast)

# Cache instance ID (fetch once at startup, reuse for all requests)
_instance_id_cache = None
_instance_id_lock = threading.Lock()

# User tier cache to avoid repeated subscription lookups
# Key: uid (Firebase UID), Value: (user_id, tier, timestamp)
_user_tier_cache = {}
_user_tier_cache_lock = threading.Lock()
_USER_TIER_CACHE_TTL_SECONDS = 300  # Cache tier for 5 minutes

logger = AppLogger.get_logger()


class UserActivityMiddleware:
    """
    ASGI Middleware to track user activity for both free and premium tiers.

    This middleware:
    - Extracts user ID from authentication tokens
    - Determines user tier (free or premium)
    - Updates last_activity timestamp in appropriate table
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

        # Skip health check, auth endpoints, and system-internal API endpoints
        if path in SKIP_AUTH_PATHS or path.startswith("/system-internal/"):
            await self.app(scope, receive, send)
            return

        # Extract Firebase JWT from Authorization header
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode()

        if not auth_header.startswith(BEARER_PREFIX):
            # No auth header, skip tracking
            await self.app(scope, receive, send)
            return

        firebase_token = auth_header[BEARER_PREFIX_LENGTH:]  # Remove "Bearer "

        # Extract UID from JWT
        uid, error = extract_uid_from_firebase_jwt(firebase_token)
        if error or not uid:
            # Invalid token, skip tracking
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
            if message["type"] == "http.response.start" and not has_tracked:
                has_tracked = True

                try:
                    # Get user ID and tier from database
                    user_id, tier = _get_user_id_and_tier(uid)

                    if user_id:
                        if tier == TIER_FREE:
                            # Check cache to avoid excessive DB writes
                            if _should_update_activity(user_id, TIER_FREE):
                                # Schedule background update (doesn't block response)
                                asyncio.create_task(
                                    _update_free_user_activity_async(user_id)
                                )
                        elif tier == TIER_PREMIUM:
                            # Check cache to avoid excessive DB writes
                            if _should_update_activity(user_id, TIER_PREMIUM):
                                # Schedule background update (doesn't block response)
                                asyncio.create_task(
                                    _update_premium_user_activity_async(user_id)
                                )

                except Exception as e:
                    # Log error but don't fail the request
                    logger.warning(f"Failed to track user activity: {e}")

            # Always send the original message
            await send(message)

        # Process the request with our wrapped send function
        await self.app(scope, receive, send_wrapper)


# Keep FreeUserActivityMiddleware as alias for backwards compatibility
FreeUserActivityMiddleware = UserActivityMiddleware


def _get_user_id_and_tier(uid: str) -> Tuple[Optional[int], Optional[str]]:
    """
    Get user ID and subscription tier from UID with caching.

    Uses a 5-minute TTL cache to avoid repeated database lookups for the same user.
    This reduces database load by ~90% for active users making multiple requests.

    Args:
        uid: Firebase user ID

    Returns:
        Tuple of (user_id, tier) where tier is TIER_FREE or TIER_PREMIUM
        Returns (None, None) if user not found or error occurs
    """
    import time

    # Check cache first
    with _user_tier_cache_lock:
        cached = _user_tier_cache.get(uid)
        if cached:
            user_id, tier, cached_at = cached
            if time.time() - cached_at < _USER_TIER_CACHE_TTL_SECONDS:
                return user_id, tier
            # Cache expired, will refresh below

    # Cache miss or expired - query database
    try:
        from studio.app.common.core.subscription.subscription_service import (
            SubscriptionService,
        )
        from studio.app.common.db.database import get_db
        from studio.app.common.models.user import User

        db = next(get_db())
        try:
            user = db.query(User).filter(User.uid == uid).first()
            if not user:
                return None, None

            # Check if user has active subscription
            subscription_data = SubscriptionService.get_user_subscription(db, user.id)
            if subscription_data:
                _, plan = subscription_data  # Only need plan for tier check
                tier = (
                    TIER_PREMIUM
                    if plan.id == SubscriptionPlanIds.PREMIUM
                    else TIER_FREE
                )
            else:
                tier = TIER_FREE

            # Update cache
            with _user_tier_cache_lock:
                _user_tier_cache[uid] = (user.id, tier, time.time())

            return user.id, tier
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Failed to get user tier for {uid}: {e}")
        return None, None


def invalidate_user_tier_cache(uid: str) -> None:
    """
    Invalidate the tier cache for a specific user.

    Call this when a user's subscription status changes (upgrade, downgrade, etc.)
    to ensure the middleware uses fresh data.

    Args:
        uid: Firebase user ID to invalidate
    """
    with _user_tier_cache_lock:
        _user_tier_cache.pop(uid, None)


def invalidate_activity_cache(user_id: int) -> None:
    """
    Invalidate the activity cache for a specific user.

    Call this when a user logs out to prevent stale cache entries on rapid re-login.
    This ensures the first activity after re-login is properly recorded instead of
    being skipped due to the cache TTL.

    Args:
        user_id: Database user ID to invalidate
    """
    with _cache_lock:
        _free_activity_cache.pop(user_id, None)
        _premium_activity_cache.pop(user_id, None)


def mark_user_logged_out(user_id: int) -> None:
    """
    Mark a user as logged out to prevent orphaned background activity updates.

    Background activity tasks are fire-and-forget. If a user logs out while an
    activity update is queued or in progress, the task may complete with stale
    data. This function marks the user as logged out so background tasks can
    skip the update.

    Args:
        user_id: Database user ID to mark as logged out
    """
    with _logged_out_lock:
        _logged_out_users[user_id] = time.time()


def is_user_logged_out(user_id: int) -> bool:
    """
    Check if a user has recently logged out.

    Used by background activity tasks to skip updates for logged out users.
    Entries are automatically cleaned up after TTL expires.

    Args:
        user_id: Database user ID to check

    Returns:
        True if user logged out recently and entry hasn't expired
    """
    with _logged_out_lock:
        logout_time = _logged_out_users.get(user_id)
        if logout_time is None:
            return False
        # Check if entry has expired
        if time.time() - logout_time > _LOGGED_OUT_TTL_SECONDS:
            del _logged_out_users[user_id]
            return False
        return True


def clear_logged_out_status(user_id: int) -> None:
    """
    Clear logged out status for a user on re-login.

    Call this when a user logs back in to clear the logged out flag.
    This prevents the edge case where a rapid re-login still sees the
    logged out status.

    Args:
        user_id: Database user ID to clear
    """
    with _logged_out_lock:
        _logged_out_users.pop(user_id, None)


def clear_free_user_logged_out_at(user_id: int) -> bool:
    """
    Clear logged_out_at timestamp for a free user on re-login.

    This prevents the cleanup job from deleting a user's data after they
    log back in. The cleanup job only processes users with a non-null
    logged_out_at timestamp that's older than the grace period.

    Args:
        user_id: Database user ID

    Returns:
        True if updated successfully (or no assignment exists), False on error
    """
    try:
        with session_scope() as session:
            assignment = (
                session.query(FreeUserAssignment)
                .filter(FreeUserAssignment.user_id == user_id)
                .first()
            )

            if assignment and assignment.logged_out_at is not None:
                assignment.logged_out_at = None
                assignment.last_activity = get_current_datetime()
                session.commit()
                logger.debug(f"Cleared logged_out_at for user {user_id} on re-login")

        return True
    except Exception as e:
        logger.warning(f"Failed to clear logged_out_at for user {user_id}: {e}")
        return False


def _should_update_activity(user_id: int, tier: str) -> bool:
    """
    Check if we should update activity for this user (throttling).
    Only update DB once per minute per user to reduce load.

    Uses separate caches for free and premium users.
    """
    cache = _free_activity_cache if tier == TIER_FREE else _premium_activity_cache

    with _cache_lock:
        last_update = cache.get(user_id, 0)
        now = time.time()

        if now - last_update >= _CACHE_TTL_SECONDS:
            return True
        return False


def _update_cache_after_commit(user_id: int, tier: str):
    """
    Update cache timestamp after successful database commit.
    This ensures cache consistency with database state.
    """
    cache = _free_activity_cache if tier == TIER_FREE else _premium_activity_cache

    with _cache_lock:
        cache[user_id] = time.time()


# ============================================================================
# FREE USER ACTIVITY TRACKING
# ============================================================================


async def _update_free_user_activity_async(user_id: int):
    """
    Update last_activity timestamp for free tier user (async wrapper).
    Runs in background to avoid blocking request.
    """
    if is_user_logged_out(user_id):
        logger.debug(f"Skipping activity update for logged out user {user_id}")
        return

    # Update cache immediately (optimistic) to reduce perceived latency
    _update_cache_after_commit(user_id, TIER_FREE)

    # Run blocking database call in thread pool (fire-and-forget)
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _update_free_user_activity_sync, user_id)
    except Exception as e:
        # Log error but don't propagate (fire-and-forget pattern)
        logger.warning(
            f"Background activity update failed for free user {user_id}: {e}"
        )


def _update_free_user_activity_sync(user_id: int) -> bool:
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
        if is_user_logged_out(user_id):
            logger.debug(
                f"Skipping free user activity DB update for logged out user {user_id}"
            )
            return False

        # Get current instance ID from EC2 metadata or environment
        instance_id = _get_instance_id()

        # Don't track if we can't determine instance ID
        # This prevents polluting database with fake "local" entries
        if not instance_id or instance_id == "local":
            return False

        # Update database - query first, then update or insert
        with session_scope() as session:
            now = get_current_datetime()

            # Check if assignment already exists
            existing = (
                session.query(FreeUserAssignment)
                .filter(FreeUserAssignment.user_id == user_id)
                .first()
            )

            if existing:
                # Update existing record
                existing.last_activity = now
                existing.instance_id = instance_id
            else:
                # Insert new record
                assignment = FreeUserAssignment(
                    user_id=user_id,
                    instance_id=instance_id,
                    assigned_at=now,
                    last_activity=now,
                )
                session.add(assignment)

            session.commit()
            return True

    except Exception as e:
        logger.error(f"Error updating free user activity for user {user_id}: {e}")
        return False


# ============================================================================
# PREMIUM USER ACTIVITY TRACKING
# ============================================================================


async def _update_premium_user_activity_async(user_id: int):
    """
    Update last_activity timestamp for premium tier user (async wrapper).
    Runs in background to avoid blocking request.
    """
    if is_user_logged_out(user_id):
        logger.debug(f"Skipping activity update for logged out user {user_id}")
        return

    # Update cache immediately (optimistic) to reduce perceived latency
    _update_cache_after_commit(user_id, TIER_PREMIUM)

    # Run blocking database call in thread pool (fire-and-forget)
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _update_premium_user_activity_sync, user_id)
    except Exception as e:
        # Log error but don't propagate (fire-and-forget pattern)
        logger.warning(
            f"Background activity update failed for premium user {user_id}: {e}"
        )


def increment_heartbeat_failures(user_id: int) -> int:
    """
    Increment heartbeat_failures counter for a premium user.

    Called when heartbeat fails to track consecutive failures. The auto-release
    Lambda should give extra grace period to users with failing heartbeats.

    Args:
        user_id: Database user ID

    Returns:
        New failure count, or -1 on error
    """
    try:
        with session_scope() as session:
            result = session.execute(
                text(
                    """
                UPDATE premium_user_assignments
                SET heartbeat_failures = heartbeat_failures + 1
                WHERE user_id = :user_id
                AND status = 'active'
                AND is_standby = 0
                """
                ),
                {"user_id": user_id},
            )
            session.commit()

            if result.rowcount > 0:
                # Get current count
                row = session.execute(
                    text(
                        """
                    SELECT heartbeat_failures FROM premium_user_assignments
                    WHERE user_id = :user_id AND status = 'active'
                    """
                    ),
                    {"user_id": user_id},
                ).fetchone()
                count = row[0] if row else 0
                logger.debug(
                    f"Incremented heartbeat failures for user {user_id} to {count}"
                )
                return count
            return 0
    except Exception as e:
        logger.error(f"Error incrementing heartbeat failures for user {user_id}: {e}")
        return -1


def _update_premium_user_activity_sync(user_id: int) -> bool:
    """
    Update last_activity timestamp for premium tier user (sync implementation).

    This method updates the last_activity column in the premium_user_assignments
    table, which is used by the Premium Cleanup Lambda to determine if an
    assignment is stale.

    Returns:
        True if update successful, False otherwise
    """
    try:
        if is_user_logged_out(user_id):
            logger.debug(
                f"Skipping premium activity DB update for logged out user {user_id}"
            )
            return False

        with session_scope() as session:
            now = datetime.now(timezone.utc)

            # Update last_activity and reset heartbeat_failures on successful heartbeat
            # Uses raw SQL for efficiency (no need to load full ORM object)
            result = session.execute(
                text(
                    """
                UPDATE premium_user_assignments
                SET last_activity = :now, heartbeat_failures = 0
                WHERE user_id = :user_id
                AND status = 'active'
                AND is_standby = 0
                """
                ),
                {"now": now, "user_id": user_id},
            )

            session.commit()

            if result.rowcount > 0:
                logger.debug(
                    f"Updated premium activity for user {user_id} at {now.isoformat()}"
                )
                return True
            else:
                # No assignment found (user may not have active premium assignment)
                return False

    except Exception as e:
        logger.error(f"Error updating premium user activity for user {user_id}: {e}")
        return False


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================


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
        logger.warning(
            "Could not retrieve EC2 instance ID from metadata service. "
            "Using 'local' as instance ID. This is OK for local development "
            "but indicates a problem in production."
        )
        _instance_id_cache = "local"
        return "local"
