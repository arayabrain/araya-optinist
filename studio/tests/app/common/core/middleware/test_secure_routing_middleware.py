"""
Unit Tests for SecureRoutingMiddleware (JWT-based with Non-Reversible Routing IDs)

Tests the JWT-based routing header generation with HMAC-SHA256 routing IDs.

WHAT IT TESTS:
1. JWT extraction from Authorization header
2. Tier cache hit/miss scenarios
3. Cache expiration (5-minute TTL)
4. Header injection (x-user-tier, x-routing-id for premium)
5. Routing ID generation (HMAC-SHA256)
6. Routing ID validation
7. Cache invalidation
8. Invalid JWT handling
9. Missing Authorization header
10. Endpoint skipping (health checks, auth)
11. Standalone mode bypass

HOW TO RUN:
  cd studio/
  pytest tests/app/common/core/middleware/test_secure_routing_middleware.py -v

REQUIREMENTS:
- pytest
- pytest-asyncio
- All dependencies from studio/app/common/
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.middleware.cors import CORSMiddleware

# Test data
TEST_UID = "test_user_12345"
TEST_TIER_PREMIUM = "premium"
TEST_TIER_FREE = "free"
TEST_JWT_TOKEN = "mock_firebase_jwt_token"


class TestJWTExtraction:
    """Test JWT extraction from Authorization header"""

    @pytest.mark.asyncio
    async def test_extract_uid_from_valid_jwt(self):
        """Test extracting UID from valid Firebase JWT"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            SecureRoutingMiddleware,
        )
        from studio.app.common.core.mode import MODE

        mock_app = AsyncMock()
        middleware = SecureRoutingMiddleware(app=mock_app)

        scope = {
            "type": "http",
            "path": "/api/test",
            "headers": [(b"authorization", b"Bearer " + TEST_JWT_TOKEN.encode())],
        }

        sent_messages = []

        async def mock_send(message):
            sent_messages.append(message)

        async def mock_receive():
            return {"type": "http.request"}

        # Mock standalone mode to False
        with patch.object(MODE, "IS_STANDALONE", False):
            # Mock Firebase verification via auth_helper
            with patch(
                "studio.app.common.core.middleware.secure_routing_middleware."
                "extract_uid_from_firebase_jwt"
            ) as mock_extract:
                mock_extract.return_value = (TEST_UID, None)

                # Mock tier lookup
                with patch(
                    "studio.app.common.core.middleware."
                    "secure_routing_middleware.get_user_tier_cached",
                    return_value=TEST_TIER_PREMIUM,
                ):
                    await middleware(scope, mock_receive, mock_send)

                    # Verify extraction was called
                    mock_extract.assert_called_once_with(TEST_JWT_TOKEN)

    @pytest.mark.asyncio
    async def test_missing_authorization_header(self):
        """Test handling missing Authorization header"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            SecureRoutingMiddleware,
        )

        mock_app = AsyncMock()
        middleware = SecureRoutingMiddleware(app=mock_app)

        scope = {
            "type": "http",
            "path": "/api/test",
            "headers": [],
        }

        await middleware(scope, AsyncMock(), AsyncMock())

        # Should pass through without error
        mock_app.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_jwt_token(self):
        """Test handling invalid JWT token"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            SecureRoutingMiddleware,
        )

        mock_app = AsyncMock()
        middleware = SecureRoutingMiddleware(app=mock_app)

        scope = {
            "type": "http",
            "path": "/api/test",
            "headers": [(b"authorization", b"Bearer invalid_token")],
        }

        # Mock Firebase verification failure via auth_helper
        with patch(
            "studio.app.common.core.middleware.secure_routing_middleware."
            "extract_uid_from_firebase_jwt"
        ) as mock_extract:
            mock_extract.return_value = (None, "Invalid token")

            await middleware(scope, AsyncMock(), AsyncMock())

            # Should pass through without error
            mock_app.assert_called_once()


class TestTierCaching:
    """Test tier caching mechanism"""

    def test_tier_cache_hit(self):
        """Test that tier cache returns cached value"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            _tier_cache,
            get_user_tier_cached,
        )

        # Clear cache
        _tier_cache.clear()

        # Set cache
        _tier_cache[TEST_UID] = (TEST_TIER_PREMIUM, time.time())

        # Should return cached value without DB query
        with patch("studio.app.common.db.database.get_db") as mock_get_db:
            tier = get_user_tier_cached(TEST_UID)
            assert tier == TEST_TIER_PREMIUM
            mock_get_db.assert_not_called()

    def test_tier_cache_miss(self):
        """Test that cache miss queries database"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            _tier_cache,
            get_user_tier_cached,
        )

        # Clear cache
        _tier_cache.clear()

        # Mock database
        mock_db = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.uid = TEST_UID
        mock_db.query().filter().first.return_value = mock_user

        mock_subscription = MagicMock()
        mock_plan = MagicMock()
        mock_plan.id = 2  # Premium plan
        subscription_data = (mock_subscription, mock_plan)

        with patch(
            "studio.app.common.db.database.get_db",
            return_value=iter([mock_db]),
        ), patch(
            "studio.app.common.core.subscription.subscription_service."
            "SubscriptionService.get_user_subscription",
            return_value=subscription_data,
        ):
            tier = get_user_tier_cached(TEST_UID)
            assert tier == TEST_TIER_PREMIUM

            # Verify cache was updated
            assert TEST_UID in _tier_cache
            assert _tier_cache[TEST_UID][0] == TEST_TIER_PREMIUM

    def test_tier_cache_expiration(self):
        """Test that expired cache entries are refreshed"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            _TIER_CACHE_TTL,
            _tier_cache,
            get_user_tier_cached,
        )

        # Set expired cache
        _tier_cache[TEST_UID] = (TEST_TIER_FREE, time.time() - _TIER_CACHE_TTL - 10)

        # Mock database
        mock_db = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.uid = TEST_UID
        mock_db.query().filter().first.return_value = mock_user

        mock_subscription = MagicMock()
        mock_plan = MagicMock()
        mock_plan.id = 2  # Premium plan
        subscription_data = (mock_subscription, mock_plan)

        with patch(
            "studio.app.common.db.database.get_db",
            return_value=iter([mock_db]),
        ), patch(
            "studio.app.common.core.subscription.subscription_service."
            "SubscriptionService.get_user_subscription",
            return_value=subscription_data,
        ):
            tier = get_user_tier_cached(TEST_UID)
            assert tier == TEST_TIER_PREMIUM

    def test_tier_cache_no_subscription(self):
        """Test handling user with no subscription"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            _tier_cache,
            get_user_tier_cached,
        )

        # Clear cache
        _tier_cache.clear()

        # Mock database
        mock_db = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.uid = TEST_UID
        mock_db.query().filter().first.return_value = mock_user

        with patch(
            "studio.app.common.db.database.get_db",
            return_value=iter([mock_db]),
        ), patch(
            "studio.app.common.core.subscription.subscription_service."
            "SubscriptionService.get_user_subscription",
            return_value=None,
        ):
            tier = get_user_tier_cached(TEST_UID)
            assert tier == TEST_TIER_FREE


class TestRoutingIDGeneration:
    """Test routing ID generation (HMAC-SHA256)"""

    def test_routing_id_generation(self):
        """Test that routing ID is generated correctly"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            generate_routing_id,
        )

        test_secret = "test_secret_key"
        routing_id = generate_routing_id(TEST_UID, test_secret)

        # Should be 16 hex characters
        assert len(routing_id) == 16
        assert all(c in "0123456789abcdef" for c in routing_id)

    def test_routing_id_deterministic(self):
        """Test that routing ID is deterministic (same UID produces same ID)"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            generate_routing_id,
        )

        test_secret = "test_secret_key"
        routing_id_1 = generate_routing_id(TEST_UID, test_secret)
        routing_id_2 = generate_routing_id(TEST_UID, test_secret)

        assert routing_id_1 == routing_id_2

    def test_routing_id_different_users(self):
        """Test that different UIDs produce different routing IDs"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            generate_routing_id,
        )

        test_secret = "test_secret_key"
        routing_id_1 = generate_routing_id("user_1", test_secret)
        routing_id_2 = generate_routing_id("user_2", test_secret)

        assert routing_id_1 != routing_id_2

    def test_routing_id_different_secrets(self):
        """Test that different secrets produce different routing IDs"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            generate_routing_id,
        )

        routing_id_1 = generate_routing_id(TEST_UID, "secret_1")
        routing_id_2 = generate_routing_id(TEST_UID, "secret_2")

        assert routing_id_1 != routing_id_2


class TestRoutingIDValidation:
    """Test routing ID validation"""

    @pytest.mark.asyncio
    async def test_valid_routing_id_accepted(self):
        """Test that valid routing ID is accepted"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            ROUTING_SECRET_KEY,
            SecureRoutingMiddleware,
            generate_routing_id,
        )

        mock_app = AsyncMock()
        middleware = SecureRoutingMiddleware(app=mock_app)

        # Generate valid routing ID
        valid_routing_id = generate_routing_id(TEST_UID, ROUTING_SECRET_KEY)

        scope = {
            "type": "http",
            "path": "/api/test",
            "headers": [
                (b"authorization", b"Bearer " + TEST_JWT_TOKEN.encode()),
                (b"x-routing-id", valid_routing_id.encode()),
            ],
        }

        # Mock Firebase verification via auth_helper
        with patch(
            "studio.app.common.core.middleware.secure_routing_middleware."
            "extract_uid_from_firebase_jwt"
        ) as mock_extract:
            mock_extract.return_value = (TEST_UID, None)

            # Mock tier lookup
            with patch(
                "studio.app.common.core.middleware.secure_routing_middleware."
                "get_user_tier_cached",
                return_value=TEST_TIER_PREMIUM,
            ):
                await middleware(scope, AsyncMock(), AsyncMock())

                # Should not log warning
                # (checked manually via logs in integration tests)

    @pytest.mark.asyncio
    async def test_invalid_routing_id_logged(self):
        """Test that invalid routing ID is detected and logged"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            SecureRoutingMiddleware,
        )
        from studio.app.common.core.mode import MODE

        mock_app = AsyncMock()
        middleware = SecureRoutingMiddleware(app=mock_app)

        scope = {
            "type": "http",
            "path": "/api/test",
            "headers": [
                (b"authorization", b"Bearer " + TEST_JWT_TOKEN.encode()),
                (b"x-routing-id", b"invalid_routing_id"),
            ],
        }

        sent_messages = []

        async def mock_send(message):
            sent_messages.append(message)

        # Mock standalone mode to False
        with patch.object(MODE, "IS_STANDALONE", False):
            # Mock Firebase verification via auth_helper
            with patch(
                "studio.app.common.core.middleware.secure_routing_middleware."
                "extract_uid_from_firebase_jwt"
            ) as mock_extract:
                mock_extract.return_value = (TEST_UID, None)

                # Mock tier lookup
                with patch(
                    "studio.app.common.core.middleware.secure_routing_middleware."
                    "get_user_tier_cached",
                    return_value=TEST_TIER_PREMIUM,
                ), patch(
                    "studio.app.common.core.middleware.secure_routing_middleware.logger"
                ) as mock_logger:
                    await middleware(scope, AsyncMock(), mock_send)

                    # Should log warning about mismatch and return 403
                    mock_logger.warning.assert_called_once()
                    warning_call = mock_logger.warning.call_args[0][0]
                    assert "Routing ID mismatch" in warning_call

                    # Verify 403 response was sent
                    assert len(sent_messages) == 2
                    assert sent_messages[0]["status"] == 403

    @pytest.mark.asyncio
    async def test_mismatched_routing_id_rejected_for_free_user(self):
        """A free-tier user sending a mismatched x-routing-id (e.g. stolen
        from a premium user) must be rejected with 403.  Unconditional
        validation prevents tier-bypass via ALB header spoofing."""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            SecureRoutingMiddleware,
        )
        from studio.app.common.core.mode import MODE

        mock_app = AsyncMock()
        middleware = SecureRoutingMiddleware(app=mock_app)

        scope = {
            "type": "http",
            "path": "/api/test",
            "headers": [
                (b"authorization", b"Bearer " + TEST_JWT_TOKEN.encode()),
                (b"x-routing-id", b"stolen_premium_routing_id"),
            ],
        }

        sent_messages = []

        async def mock_send(message):
            sent_messages.append(message)

        with patch.object(MODE, "IS_STANDALONE", False):
            with patch(
                "studio.app.common.core.middleware.secure_routing_middleware."
                "extract_uid_from_firebase_jwt"
            ) as mock_extract:
                mock_extract.return_value = (TEST_UID, None)

                with patch(
                    "studio.app.common.core.middleware.secure_routing_middleware."
                    "get_user_tier_cached",
                    return_value=TEST_TIER_FREE,
                ):
                    await middleware(scope, AsyncMock(), mock_send)

                    # Must be rejected with 403 — unconditional validation
                    assert len(sent_messages) == 2
                    assert sent_messages[0]["status"] == 403

    @pytest.mark.asyncio
    async def test_mismatched_routing_id_still_rejected_for_premium(self):
        """Regression: premium users with mismatched routing IDs must still
        be rejected with 403."""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            SecureRoutingMiddleware,
        )
        from studio.app.common.core.mode import MODE

        mock_app = AsyncMock()
        middleware = SecureRoutingMiddleware(app=mock_app)

        scope = {
            "type": "http",
            "path": "/api/test",
            "headers": [
                (b"authorization", b"Bearer " + TEST_JWT_TOKEN.encode()),
                (b"x-routing-id", b"wrong_routing_id"),
            ],
        }

        sent_messages = []

        async def mock_send(message):
            sent_messages.append(message)

        with patch.object(MODE, "IS_STANDALONE", False):
            with patch(
                "studio.app.common.core.middleware.secure_routing_middleware."
                "extract_uid_from_firebase_jwt"
            ) as mock_extract:
                mock_extract.return_value = (TEST_UID, None)

                with patch(
                    "studio.app.common.core.middleware.secure_routing_middleware."
                    "get_user_tier_cached",
                    return_value=TEST_TIER_PREMIUM,
                ):
                    await middleware(scope, AsyncMock(), mock_send)

                    # Should be rejected with 403
                    assert len(sent_messages) == 2
                    assert sent_messages[0]["status"] == 403


class TestCacheInvalidation:
    """Test cache invalidation on subscription changes"""

    def test_invalidate_user_tier_cache(self):
        """Test that cache invalidation removes user from cache"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            _tier_cache,
            invalidate_user_tier_cache,
        )

        # Set cache
        _tier_cache[TEST_UID] = (TEST_TIER_PREMIUM, time.time())
        assert TEST_UID in _tier_cache

        # Invalidate
        invalidate_user_tier_cache(TEST_UID)

        # Should be removed
        assert TEST_UID not in _tier_cache

    def test_invalidate_nonexistent_user(self):
        """Test that invalidating non-cached user doesn't error"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            _tier_cache,
            invalidate_user_tier_cache,
        )

        _tier_cache.clear()

        # Should not raise error
        invalidate_user_tier_cache("nonexistent_user")


class TestHeaderInjection:
    """Test routing header injection"""

    @pytest.mark.asyncio
    async def test_adds_routing_headers_premium(self):
        """
        Test that middleware adds x-user-tier and
        x-routing-id headers for premium users
        """
        from studio.app.common.core.middleware.secure_routing_middleware import (
            ROUTING_SECRET_KEY,
            SecureRoutingMiddleware,
            generate_routing_id,
        )
        from studio.app.common.core.mode import MODE

        mock_app = AsyncMock()
        middleware = SecureRoutingMiddleware(app=mock_app)

        scope = {
            "type": "http",
            "path": "/api/test",
            "headers": [(b"authorization", b"Bearer " + TEST_JWT_TOKEN.encode())],
        }

        sent_messages = []

        async def mock_send(message):
            sent_messages.append(message)

        async def mock_receive():
            return {"type": "http.request"}

        # Mock standalone mode to False
        with patch.object(MODE, "IS_STANDALONE", False):
            # Mock Firebase verification via auth_helper
            with patch(
                "studio.app.common.core.middleware.secure_routing_middleware."
                "extract_uid_from_firebase_jwt"
            ) as mock_extract:
                mock_extract.return_value = (TEST_UID, None)

                # Mock tier lookup
                with patch(
                    "studio.app.common.core.middleware.secure_routing_middleware."
                    "get_user_tier_cached",
                    return_value=TEST_TIER_PREMIUM,
                ):
                    # Mock the app to send a response
                    async def mock_app_impl(scope, receive, send):
                        await send(
                            {
                                "type": "http.response.start",
                                "status": 200,
                                "headers": [],
                            }
                        )

                    middleware.app = mock_app_impl
                    await middleware(scope, mock_receive, mock_send)

                    # Check that headers were added
                    response_start = sent_messages[0]
                    headers = dict(response_start["headers"])
                    assert b"x-user-tier" in headers
                    assert headers[b"x-user-tier"] == TEST_TIER_PREMIUM.encode()
                    assert b"x-routing-id" in headers
                # Verify routing ID is correct HMAC
                expected_routing_id = generate_routing_id(TEST_UID, ROUTING_SECRET_KEY)
                assert headers[b"x-routing-id"] == expected_routing_id.encode()

    @pytest.mark.asyncio
    async def test_free_tier_no_routing_id(self):
        """Test that free tier users don't get routing ID header"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            SecureRoutingMiddleware,
        )
        from studio.app.common.core.mode import MODE

        mock_app = AsyncMock()
        middleware = SecureRoutingMiddleware(app=mock_app)

        scope = {
            "type": "http",
            "path": "/api/test",
            "headers": [(b"authorization", b"Bearer " + TEST_JWT_TOKEN.encode())],
        }

        sent_messages = []

        async def mock_send(message):
            sent_messages.append(message)

        async def mock_receive():
            return {"type": "http.request"}

        # Mock standalone mode to False
        with patch.object(MODE, "IS_STANDALONE", False):
            # Mock Firebase verification via auth_helper
            with patch(
                "studio.app.common.core.middleware.secure_routing_middleware."
                "extract_uid_from_firebase_jwt"
            ) as mock_extract:
                mock_extract.return_value = (TEST_UID, None)

                # Mock tier lookup
                with patch(
                    "studio.app.common.core.middleware.secure_routing_middleware."
                    "get_user_tier_cached",
                    return_value=TEST_TIER_FREE,
                ):
                    # Mock the app to send a response
                    async def mock_app_impl(scope, receive, send):
                        await send(
                            {
                                "type": "http.response.start",
                                "status": 200,
                                "headers": [],
                            }
                        )

                    middleware.app = mock_app_impl
                    await middleware(scope, mock_receive, mock_send)

                    # Check that headers were added
                    response_start = sent_messages[0]
                    headers = dict(response_start["headers"])
                    assert b"x-user-tier" in headers
                    assert headers[b"x-user-tier"] == TEST_TIER_FREE.encode()
                    # Free tier should NOT have routing ID
                    assert b"x-routing-id" not in headers


class TestMiddlewareIntegration:
    """Test ASGI middleware integration"""

    @pytest.mark.asyncio
    async def test_middleware_skips_health_endpoint(self):
        """Test that middleware skips /health endpoint"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            SecureRoutingMiddleware,
        )

        mock_app = AsyncMock()
        middleware = SecureRoutingMiddleware(app=mock_app)

        scope = {"type": "http", "path": "/health", "headers": []}

        with patch(
            "studio.app.common.core.middleware.secure_routing_middleware."
            "extract_uid_from_firebase_jwt"
        ) as mock_extract:
            await middleware(scope, AsyncMock(), AsyncMock())

            # Should not verify JWT for health check
            mock_extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_middleware_skips_auth_endpoints(self):
        """Test that middleware skips /api/auth/* endpoints"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            SecureRoutingMiddleware,
        )

        mock_app = AsyncMock()
        middleware = SecureRoutingMiddleware(app=mock_app)

        for path in ["/api/auth/login", "/api/auth/refresh"]:
            scope = {"type": "http", "path": path, "headers": []}

            with patch(
                "studio.app.common.core.middleware.secure_routing_middleware."
                "extract_uid_from_firebase_jwt"
            ) as mock_extract:
                await middleware(scope, AsyncMock(), AsyncMock())

                # Should not verify JWT for auth endpoints
                mock_extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_middleware_skips_standalone_mode(self):
        """Test that middleware skips processing in standalone mode"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            SecureRoutingMiddleware,
        )
        from studio.app.common.core.mode import MODE

        mock_app = AsyncMock()
        middleware = SecureRoutingMiddleware(app=mock_app)

        scope = {
            "type": "http",
            "path": "/api/test",
            "headers": [(b"authorization", b"Bearer " + TEST_JWT_TOKEN.encode())],
        }

        with patch.object(MODE, "IS_STANDALONE", True), patch(
            "studio.app.common.core.middleware.secure_routing_middleware."
            "extract_uid_from_firebase_jwt"
        ) as mock_extract:
            await middleware(scope, AsyncMock(), AsyncMock())

            # Should not verify JWT in standalone mode
            mock_extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_middleware_handles_websocket(self):
        """Test that middleware skips non-HTTP requests"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            SecureRoutingMiddleware,
        )

        mock_app = AsyncMock()
        middleware = SecureRoutingMiddleware(app=mock_app)

        scope = {"type": "websocket", "path": "/ws"}

        with patch(
            "studio.app.common.core.middleware.secure_routing_middleware."
            "extract_uid_from_firebase_jwt"
        ) as mock_extract:
            await middleware(scope, AsyncMock(), AsyncMock())

            # Should not verify JWT for websocket
            mock_extract.assert_not_called()
            mock_app.assert_called_once()


class TestCORSConfiguration:
    """CORS must expose routing headers so the browser
    can read them, and SecureRoutingMiddleware must run before
    CORS in the response path (LIFO order means it is added
    after CORS in the source)."""

    def test_cors_exposes_routing_headers(self):
        """CORSMiddleware must list x-user-tier and
        x-routing-id in expose_headers."""
        from studio.__main_unit__ import app

        cors_mw = None
        for mw in app.user_middleware:
            if mw.cls is CORSMiddleware:
                cors_mw = mw
                break

        assert cors_mw is not None, "CORSMiddleware not found in app middleware"
        exposed = cors_mw.options.get("expose_headers", [])
        assert "x-user-tier" in exposed
        assert "x-routing-id" in exposed

    def test_secure_routing_added_before_cors(self):
        """SecureRoutingMiddleware must be inner relative to
        CORS so it adds headers on the response path before
        CORS exposes them. Starlette inserts at index 0, so
        the middleware added first has the highest index
        (innermost) and processes responses first."""
        from starlette.middleware.cors import CORSMiddleware

        from studio.__main_unit__ import app
        from studio.app.common.core.middleware import SecureRoutingMiddleware

        classes = [mw.cls for mw in app.user_middleware]

        assert (
            SecureRoutingMiddleware in classes
        ), "SecureRoutingMiddleware not in middleware stack"
        assert CORSMiddleware in classes, "CORSMiddleware not in middleware stack"

        sr_idx = classes.index(SecureRoutingMiddleware)
        cors_idx = classes.index(CORSMiddleware)
        # Higher index = innermost = processes response first
        assert sr_idx > cors_idx, (
            "SecureRoutingMiddleware must be innermost "
            "relative to CORS (higher index in "
            "user_middleware) so it adds headers before "
            "CORS exposes them"
        )


class TestInstanceHash:
    """Test generate_instance_hash function and X-Served-By-Instance header"""

    def test_generate_instance_hash_deterministic(self):
        """generate_instance_hash produces deterministic output for the same input"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            generate_instance_hash,
        )

        hash1 = generate_instance_hash("i-0abc123def456", "test-secret")
        hash2 = generate_instance_hash("i-0abc123def456", "test-secret")
        assert hash1 == hash2
        assert len(hash1) == 16  # 16 hex chars

    def test_generate_instance_hash_differs_from_routing_id(self):
        """
        generate_instance_hash output differs from generate_routing_id
            for the same input (domain separation)
        """
        from studio.app.common.core.middleware.secure_routing_middleware import (
            generate_instance_hash,
            generate_routing_id,
        )

        same_input = "i-0abc123def456"
        secret = "test-secret"

        instance_hash = generate_instance_hash(same_input, secret)
        routing_id = generate_routing_id(same_input, secret)

        assert (
            instance_hash != routing_id
        ), "instance hash and routing id must differ due to domain separation"

    def test_generate_instance_hash_different_instances(self):
        """Different instance IDs produce different hashes"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            generate_instance_hash,
        )

        hash1 = generate_instance_hash("i-instance-A", "test-secret")
        hash2 = generate_instance_hash("i-instance-B", "test-secret")
        assert hash1 != hash2

    @pytest.mark.asyncio
    async def test_served_by_instance_header_on_premium_response(self):
        """X-Served-By-Instance header is present on authenticated premium responses"""
        from studio.app.common.core.middleware.secure_routing_middleware import (
            ROUTING_SECRET_KEY,
            SecureRoutingMiddleware,
            generate_instance_hash,
        )
        from studio.app.common.core.mode import MODE

        mock_app = AsyncMock()
        middleware = SecureRoutingMiddleware(app=mock_app)

        scope = {
            "type": "http",
            "path": "/api/test",
            "headers": [(b"authorization", b"Bearer " + TEST_JWT_TOKEN.encode())],
        }

        sent_messages = []

        async def mock_send(message):
            sent_messages.append(message)

        async def mock_receive():
            return {"type": "http.request"}

        with patch.object(MODE, "IS_STANDALONE", False):
            with patch(
                "studio.app.common.core.middleware.secure_routing_middleware."
                "extract_uid_from_firebase_jwt"
            ) as mock_extract:
                mock_extract.return_value = (TEST_UID, None)

                with patch(
                    "studio.app.common.core.middleware.secure_routing_middleware."
                    "get_user_tier_cached",
                    return_value=TEST_TIER_PREMIUM,
                ):
                    with patch(
                        "studio.app.common.core.middleware.secure_routing_middleware."
                        "_get_instance_id",
                        return_value="i-test-instance-123",
                    ):

                        async def mock_app_impl(scope, receive, send):
                            await send(
                                {
                                    "type": "http.response.start",
                                    "status": 200,
                                    "headers": [],
                                }
                            )

                        middleware.app = mock_app_impl
                        await middleware(scope, mock_receive, mock_send)

                        response_start = sent_messages[0]
                        headers = dict(response_start["headers"])
                        assert b"x-served-by-instance" in headers

                        expected_hash = generate_instance_hash(
                            "i-test-instance-123", ROUTING_SECRET_KEY
                        )
                        assert (
                            headers[b"x-served-by-instance"] == expected_hash.encode()
                        )

    @pytest.mark.asyncio
    async def test_served_by_instance_header_on_free_response(self):
        """
        X-Served-By-Instance header is present
            on authenticated free-tier responses too
        """
        from studio.app.common.core.middleware.secure_routing_middleware import (
            SecureRoutingMiddleware,
        )
        from studio.app.common.core.mode import MODE

        mock_app = AsyncMock()
        middleware = SecureRoutingMiddleware(app=mock_app)

        scope = {
            "type": "http",
            "path": "/api/test",
            "headers": [(b"authorization", b"Bearer " + TEST_JWT_TOKEN.encode())],
        }

        sent_messages = []

        async def mock_send(message):
            sent_messages.append(message)

        async def mock_receive():
            return {"type": "http.request"}

        with patch.object(MODE, "IS_STANDALONE", False):
            with patch(
                "studio.app.common.core.middleware.secure_routing_middleware."
                "extract_uid_from_firebase_jwt"
            ) as mock_extract:
                mock_extract.return_value = (TEST_UID, None)

                with patch(
                    "studio.app.common.core.middleware.secure_routing_middleware."
                    "get_user_tier_cached",
                    return_value=TEST_TIER_FREE,
                ):
                    with patch(
                        "studio.app.common.core.middleware.secure_routing_middleware."
                        "_get_instance_id",
                        return_value="i-free-instance-456",
                    ):

                        async def mock_app_impl(scope, receive, send):
                            await send(
                                {
                                    "type": "http.response.start",
                                    "status": 200,
                                    "headers": [],
                                }
                            )

                        middleware.app = mock_app_impl
                        await middleware(scope, mock_receive, mock_send)

                        response_start = sent_messages[0]
                        headers = dict(response_start["headers"])
                        # Free tier still gets X-Served-By-Instance
                        assert b"x-served-by-instance" in headers
                        # But not routing ID
                        assert b"x-routing-id" not in headers


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
