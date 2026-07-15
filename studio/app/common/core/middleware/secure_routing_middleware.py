"""
Secure Routing Middleware

This middleware extracts user identity from Firebase JWT tokens and adds routing
headers for ALB-based tier routing. It prevents header spoofing by using
cryptographically secure, non-reversible routing IDs.

Security Features:
- Reuses Firebase JWT validation (cryptographically signed)
- Non-reversible routing IDs (HMAC-SHA256) prevent UID exposure
- Backend-controlled routing headers eliminate client spoofing
- Tier caching reduces database queries (5-minute TTL)
- Immediate cache invalidation on subscription changes

Architecture:
1. Extract Firebase JWT from Authorization header
2. Validate JWT and extract UID (reuses existing auth infrastructure)
3. Query user tier with caching (webhook-triggered invalidation)
4. Generate non-reversible routing_id from UID (HMAC-SHA256)
5. Add x-user-tier and x-routing-id headers for ALB routing
6. Validate routing_id on incoming requests (reject with 403 if spoofed)
"""

import hashlib
import hmac
import os
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from studio.app.common.core.auth.auth_helper import extract_uid_from_firebase_jwt
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.middleware.constants import SKIP_AUTH_PATHS
from studio.app.common.core.middleware.user_activity_middleware import (
    TIER_FREE,
    TIER_PREMIUM,
    _get_instance_id,
)
from studio.app.common.core.mode import MODE
from studio.app.common.core.subscription.constants import SubscriptionPlanIds

logger = AppLogger.get_logger()

# Routing secret key for generating non-reversible routing IDs
ROUTING_SECRET_KEY = os.environ.get("ROUTING_SECRET_KEY", "dev-key-not-for-production")

# Tier cache: {uid: (tier, timestamp)}
_tier_cache = {}
_TIER_CACHE_TTL = 300  # 5 minutes
_MAX_CACHE_SIZE = 10000  # Prevent unbounded growth

# Instance hash cache: instance_id is stable per process, so cache avoids
# recomputing HMAC on every request and lets us log the mapping exactly once.
_instance_hash_cache: dict[str, str] = {}


def generate_instance_hash(instance_id: str, secret_key: str) -> str:
    """Generate non-reversible hash of instance ID using HMAC-SHA256.

    Uses domain-separated key ("instance:" prefix) to ensure the output
    never collides with routing IDs generated from UIDs.

    Args:
        instance_id: EC2 instance ID (e.g., "i-0abc123def456")
        secret_key: Secret key for HMAC signature

    Returns:
        16-character hex string (64 bits of entropy)
    """
    domain_key = f"instance:{secret_key}"
    signature = hmac.new(
        domain_key.encode("utf-8"), instance_id.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return signature[:16]


def get_instance_hash_cached(instance_id: str, secret_key: str) -> str:
    """Return cached instance hash, logging the mapping on first computation.

    The mapping is logged exactly once per instance_id so that operators can
    trace an X-Served-By-Instance header value back to the raw EC2 instance ID
    during incident investigation.  The secret_key is not included in the cache
    key because ROUTING_SECRET_KEY is constant within a process.  The raw
    instance ID is safe to include in backend logs — the HMAC exists only to
    avoid exposing it to the browser client.
    """
    if instance_id in _instance_hash_cache:
        return _instance_hash_cache[instance_id]

    instance_hash = generate_instance_hash(instance_id, secret_key)
    _instance_hash_cache[instance_id] = instance_hash
    logger.info(
        "[routing] instance hash mapping: instance_id=%s -> " "x-served-by-instance=%s",
        instance_id,
        instance_hash,
    )
    return instance_hash


def generate_routing_id(uid: str, secret_key: str) -> str:
    """Generate non-reversible routing ID from UID using HMAC-SHA256

    This function creates a cryptographically secure, non-reversible identifier
    from the user's UID. The routing ID:
    - Cannot be reverse-engineered to extract the UID
    - Is deterministic (same UID always produces same routing_id)
    - Requires the secret key to generate (client cannot forge)

    Args:
        uid: Firebase user ID
        secret_key: Secret key for HMAC signature

    Returns:
        16-character hex string (64 bits of entropy)
    """
    signature = hmac.new(
        secret_key.encode("utf-8"), uid.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return signature[:16]  # 16 hex chars = 64 bits


def invalidate_user_tier_cache(uid: str) -> None:
    """Invalidate tier cache for specific user

    Called by subscription webhooks when a user's tier changes (upgrade/downgrade).
    Ensures next request fetches fresh tier from database.

    Args:
        uid: Firebase user ID to invalidate
    """
    if uid in _tier_cache:
        del _tier_cache[uid]
        logger.info("Invalidated tier cache for user")


def get_user_tier_cached(uid: str) -> str:
    """Get user tier with 5-minute cache

    Args:
        uid: Firebase user ID

    Returns:
        Subscription tier ('premium' or 'free')
    """
    current_time = time.time()

    # Cleanup old cache entries if cache is too large
    if len(_tier_cache) > _MAX_CACHE_SIZE:
        expired_keys = [
            k
            for k, (_, ts) in _tier_cache.items()
            if current_time - ts >= _TIER_CACHE_TTL
        ]
        for key in expired_keys:
            del _tier_cache[key]

    # Check cache
    if uid in _tier_cache:
        tier, timestamp = _tier_cache[uid]
        if current_time - timestamp < _TIER_CACHE_TTL:
            return tier

    # Cache miss - query database
    # Note: Some imports inside function to avoid circular imports at module load time
    # (SubscriptionService, get_db, User have dependencies that could cause cycles)
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
                return TIER_FREE

            # Check if user has active subscription
            subscription_data = SubscriptionService.get_user_subscription(db, user.id)
            if subscription_data:
                _, plan = subscription_data
                tier = (
                    TIER_PREMIUM
                    if plan.id == SubscriptionPlanIds.PREMIUM
                    else TIER_FREE
                )
            else:
                tier = TIER_FREE

            # Update cache
            _tier_cache[uid] = (tier, current_time)
            return tier
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Failed to query user tier for {uid}: {e}")
        return TIER_FREE


class SecureRoutingMiddleware:
    """
    ASGI Middleware to add secure routing headers based on Firebase JWT validation.

    This middleware:
    - Extracts Firebase JWT from Authorization header
    - Validates JWT and extracts UID (reuses existing auth)
    - Queries user tier with caching (webhook-triggered invalidation)
    - Generates non-reversible routing_id from UID (HMAC-SHA256)
    - Adds x-user-tier and x-routing-id headers for ALB routing
    - Validates incoming routing_id headers and rejects mismatches with 403

    Security:
    - UID never exposed to client (only non-reversible routing_id)
    - Client cannot forge routing_id without SECRET_KEY
    - Backend validates routing_id matches JWT UID and rejects spoofing attempts
    - Immediate cache invalidation on subscription changes
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Skip in standalone mode
        if MODE.IS_STANDALONE:
            await self.app(scope, receive, send)
            return

        # Get request path
        path = scope.get("path", "")

        # Skip health check, auth endpoints, and system-internal API endpoints
        # System-internal endpoints use their own auth (shared secret, not JWT)
        if path in SKIP_AUTH_PATHS or path.startswith("/system-internal/"):
            await self.app(scope, receive, send)
            return

        # Get Firebase JWT from Authorization header
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode()

        if not auth_header.startswith("Bearer "):
            await self.app(scope, receive, send)
            return

        firebase_token = auth_header[7:]  # Remove "Bearer "

        # Extract UID from JWT (reuses existing auth infrastructure)
        uid, error = extract_uid_from_firebase_jwt(firebase_token)
        if error:
            logger.debug(f"Failed to verify Firebase token: {error}")

        if not uid:
            await self.app(scope, receive, send)
            return

        # Get tier with caching
        tier = get_user_tier_cached(uid)

        # Validate routing ID if present in request (detect header spoofing).
        # Unconditional for all tiers — prevents a free-tier caller from
        # attaching a stolen premium routing_id and being routed to a
        # premium-dedicated instance by the ALB.
        routing_id_header = headers.get(b"x-routing-id", b"").decode()
        if routing_id_header:
            expected_routing_id = generate_routing_id(uid, ROUTING_SECRET_KEY)
            if routing_id_header != expected_routing_id:
                logger.warning(
                    f"Routing ID mismatch detected - rejecting request. "
                    f"Expected: {expected_routing_id[:8]}..., "
                    f"Got: {routing_id_header[:8]}..."
                )
                # Reject with 403 for stricter security
                response = {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [(b"content-type", b"application/json")],
                }
                await send(response)
                body = {
                    "type": "http.response.body",
                    "body": b'{"detail":"Invalid routing ID"}',
                }
                await send(body)
                return

        # Add routing headers for ALB
        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-user-tier", tier.encode()))

                # Only add routing ID for premium users
                if tier == TIER_PREMIUM:
                    routing_id = generate_routing_id(uid, ROUTING_SECRET_KEY)
                    headers.append((b"x-routing-id", routing_id.encode()))

                # Add instance identity header for all authenticated responses.
                # The frontend compares this against the expected instance hash
                # to detect ALB fallback.
                instance_id = _get_instance_id()
                if instance_id:
                    instance_hash = get_instance_hash_cached(
                        instance_id, ROUTING_SECRET_KEY
                    )
                    headers.append((b"x-served-by-instance", instance_hash.encode()))

                message["headers"] = headers

            await send(message)

        await self.app(scope, receive, send_wrapper)
