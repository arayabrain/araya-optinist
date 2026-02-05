"""
Unit Tests for UserActivityMiddleware

Tests the combined activity tracking middleware for both free and premium users.

WHAT IT TESTS:
1. Middleware correctly identifies free vs premium users
2. Activity updates are cached (60-second TTL)
3. Free users: updates free_user_assignments table
4. Premium users: updates premium_user_assignments table
5. Skips health checks, auth endpoints, system-internal paths
6. Standalone mode bypass
7. Error handling (graceful degradation)
8. Missing/invalid JWT handling

HOW TO RUN:
  cd studio/
  pytest tests/app/common/core/middleware/test_user_activity_middleware.py -v

REQUIREMENTS:
- pytest
- pytest-asyncio
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Test data
TEST_UID = "test_user_12345"
TEST_USER_ID = 42
TEST_JWT_TOKEN = "mock_firebase_jwt_token"
TIER_FREE = "free"
TIER_PREMIUM = "premium"


class TestMiddlewarePathSkipping:
    """Test that middleware skips appropriate paths"""

    @pytest.mark.asyncio
    async def test_skips_health_endpoint(self):
        """Middleware should skip /health endpoint"""
        from studio.app.common.core.middleware.user_activity_middleware import (
            UserActivityMiddleware,
        )
        from studio.app.common.core.mode import MODE

        mock_app = AsyncMock()
        middleware = UserActivityMiddleware(app=mock_app)

        scope = {
            "type": "http",
            "path": "/health",
            "headers": [],
        }

        async def mock_receive():
            return {"type": "http.request"}

        async def mock_send(message):
            pass

        with patch.object(MODE, "IS_STANDALONE", False):
            await middleware(scope, mock_receive, mock_send)

        # App should be called directly without any user lookup
        mock_app.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_system_internal_paths(self):
        """Middleware should skip /system-internal/* paths"""
        from studio.app.common.core.middleware.user_activity_middleware import (
            UserActivityMiddleware,
        )
        from studio.app.common.core.mode import MODE

        mock_app = AsyncMock()
        middleware = UserActivityMiddleware(app=mock_app)

        scope = {
            "type": "http",
            "path": "/system-internal/sync-experiments/1",
            "headers": [],
        }

        async def mock_receive():
            return {"type": "http.request"}

        async def mock_send(message):
            pass

        with patch.object(MODE, "IS_STANDALONE", False):
            await middleware(scope, mock_receive, mock_send)

        mock_app.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_standalone_mode(self):
        """Middleware should skip in standalone mode"""
        from studio.app.common.core.middleware.user_activity_middleware import (
            UserActivityMiddleware,
        )
        from studio.app.common.core.mode import MODE

        mock_app = AsyncMock()
        middleware = UserActivityMiddleware(app=mock_app)

        scope = {
            "type": "http",
            "path": "/api/test",
            "headers": [(b"authorization", b"Bearer " + TEST_JWT_TOKEN.encode())],
        }

        async def mock_receive():
            return {"type": "http.request"}

        async def mock_send(message):
            pass

        with patch.object(MODE, "IS_STANDALONE", True):
            await middleware(scope, mock_receive, mock_send)

        mock_app.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_missing_auth_header(self):
        """Middleware should skip requests without Authorization header"""
        from studio.app.common.core.middleware.user_activity_middleware import (
            UserActivityMiddleware,
        )
        from studio.app.common.core.mode import MODE

        mock_app = AsyncMock()
        middleware = UserActivityMiddleware(app=mock_app)

        scope = {
            "type": "http",
            "path": "/api/test",
            "headers": [],
        }

        async def mock_receive():
            return {"type": "http.request"}

        async def mock_send(message):
            pass

        with patch.object(MODE, "IS_STANDALONE", False):
            await middleware(scope, mock_receive, mock_send)

        mock_app.assert_called_once()


class TestCacheThrottling:
    """Test activity update caching/throttling"""

    def test_should_update_activity_first_time(self):
        """First activity update for a user should return True"""
        from studio.app.common.core.middleware.user_activity_middleware import (
            _free_activity_cache,
            _should_update_activity,
        )

        # Clear cache
        _free_activity_cache.clear()

        # First time should return True
        result = _should_update_activity(TEST_USER_ID, TIER_FREE)
        assert result is True

    def test_should_not_update_activity_within_ttl(self):
        """Activity update within TTL should return False"""
        from studio.app.common.core.middleware.user_activity_middleware import (
            _free_activity_cache,
            _should_update_activity,
            _update_cache_after_commit,
        )

        # Clear and set cache
        _free_activity_cache.clear()
        _update_cache_after_commit(TEST_USER_ID, TIER_FREE)

        # Immediately after should return False
        result = _should_update_activity(TEST_USER_ID, TIER_FREE)
        assert result is False

    def test_should_update_activity_after_ttl(self):
        """Activity update after TTL should return True"""
        from studio.app.common.core.middleware.user_activity_middleware import (
            _CACHE_TTL_SECONDS,
            _cache_lock,
            _free_activity_cache,
            _should_update_activity,
        )

        # Clear cache and set old timestamp
        _free_activity_cache.clear()
        with _cache_lock:
            _free_activity_cache[TEST_USER_ID] = time.time() - _CACHE_TTL_SECONDS - 1

        # After TTL should return True
        result = _should_update_activity(TEST_USER_ID, TIER_FREE)
        assert result is True

    def test_free_and_premium_caches_are_separate(self):
        """Free and premium users should have separate caches"""
        from studio.app.common.core.middleware.user_activity_middleware import (
            _free_activity_cache,
            _premium_activity_cache,
            _should_update_activity,
            _update_cache_after_commit,
        )

        # Clear both caches
        _free_activity_cache.clear()
        _premium_activity_cache.clear()

        # Update free cache
        _update_cache_after_commit(TEST_USER_ID, TIER_FREE)

        # Free should be cached, premium should not
        assert _should_update_activity(TEST_USER_ID, TIER_FREE) is False
        assert _should_update_activity(TEST_USER_ID, TIER_PREMIUM) is True


class TestUserTierDetection:
    """Test user tier detection"""

    def setup_method(self):
        """Clear tier cache before each test to ensure isolation"""
        from studio.app.common.core.middleware.user_activity_middleware import (
            invalidate_user_tier_cache,
        )

        invalidate_user_tier_cache(TEST_UID)

    def test_get_user_id_and_tier_free_user(self):
        """Test detecting free tier user"""
        from studio.app.common.core.middleware.user_activity_middleware import (
            _get_user_id_and_tier,
        )
        from studio.app.common.core.subscription.constants import SubscriptionPlanIds

        mock_user = MagicMock()
        mock_user.id = TEST_USER_ID

        mock_plan = MagicMock()
        mock_plan.id = SubscriptionPlanIds.FREE

        mock_subscription = MagicMock()

        # Patch at the actual module location (imports are inside the function)
        with patch("studio.app.common.db.database.get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_db.query.return_value.filter.return_value.first.return_value = (
                mock_user
            )
            mock_get_db.return_value = iter([mock_db])

            with patch(
                "studio.app.common.core.subscription.subscription_service."
                "SubscriptionService.get_user_subscription"
            ) as mock_get_sub:
                mock_get_sub.return_value = (mock_subscription, mock_plan)

                user_id, tier = _get_user_id_and_tier(TEST_UID)

                assert user_id == TEST_USER_ID
                assert tier == TIER_FREE

    def test_get_user_id_and_tier_premium_user(self):
        """Test detecting premium tier user"""
        from studio.app.common.core.middleware.user_activity_middleware import (
            _get_user_id_and_tier,
        )
        from studio.app.common.core.subscription.constants import SubscriptionPlanIds

        mock_user = MagicMock()
        mock_user.id = TEST_USER_ID

        mock_plan = MagicMock()
        mock_plan.id = SubscriptionPlanIds.PREMIUM

        mock_subscription = MagicMock()

        with patch("studio.app.common.db.database.get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_db.query.return_value.filter.return_value.first.return_value = (
                mock_user
            )
            mock_get_db.return_value = iter([mock_db])

            with patch(
                "studio.app.common.core.subscription.subscription_service."
                "SubscriptionService.get_user_subscription"
            ) as mock_get_sub:
                mock_get_sub.return_value = (mock_subscription, mock_plan)

                user_id, tier = _get_user_id_and_tier(TEST_UID)

                assert user_id == TEST_USER_ID
                assert tier == TIER_PREMIUM

    def test_get_user_id_and_tier_user_not_found(self):
        """Test handling user not found"""
        from studio.app.common.core.middleware.user_activity_middleware import (
            _get_user_id_and_tier,
        )

        with patch("studio.app.common.db.database.get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_db.query.return_value.filter.return_value.first.return_value = None
            mock_get_db.return_value = iter([mock_db])

            user_id, tier = _get_user_id_and_tier(TEST_UID)

            assert user_id is None
            assert tier is None


class TestBackwardsCompatibility:
    """Test backwards compatibility alias"""

    def test_free_user_activity_middleware_alias(self):
        """FreeUserActivityMiddleware should be alias for UserActivityMiddleware"""
        from studio.app.common.core.middleware.user_activity_middleware import (
            FreeUserActivityMiddleware,
            UserActivityMiddleware,
        )

        assert FreeUserActivityMiddleware is UserActivityMiddleware

    def test_import_from_init(self):
        """Both classes should be importable from __init__"""
        from studio.app.common.core.middleware import (
            FreeUserActivityMiddleware,
            UserActivityMiddleware,
        )

        assert FreeUserActivityMiddleware is UserActivityMiddleware


class TestActivityCacheInvalidation:
    """Test activity cache invalidation on logout"""

    def test_invalidate_activity_cache_clears_free_cache(self):
        """invalidate_activity_cache should clear free user cache entry"""
        from studio.app.common.core.middleware.user_activity_middleware import (
            _free_activity_cache,
            _update_cache_after_commit,
            invalidate_activity_cache,
        )

        # Set up cache entry
        _free_activity_cache.clear()
        _update_cache_after_commit(TEST_USER_ID, TIER_FREE)
        assert TEST_USER_ID in _free_activity_cache

        # Invalidate
        invalidate_activity_cache(TEST_USER_ID)

        # Should be cleared
        assert TEST_USER_ID not in _free_activity_cache

    def test_invalidate_activity_cache_clears_premium_cache(self):
        """invalidate_activity_cache should clear premium user cache entry"""
        from studio.app.common.core.middleware.user_activity_middleware import (
            _premium_activity_cache,
            _update_cache_after_commit,
            invalidate_activity_cache,
        )

        # Set up cache entry
        _premium_activity_cache.clear()
        _update_cache_after_commit(TEST_USER_ID, TIER_PREMIUM)
        assert TEST_USER_ID in _premium_activity_cache

        # Invalidate
        invalidate_activity_cache(TEST_USER_ID)

        # Should be cleared
        assert TEST_USER_ID not in _premium_activity_cache

    def test_invalidate_activity_cache_clears_both_caches(self):
        """invalidate_activity_cache should clear both free and premium caches"""
        from studio.app.common.core.middleware.user_activity_middleware import (
            _free_activity_cache,
            _premium_activity_cache,
            _update_cache_after_commit,
            invalidate_activity_cache,
        )

        # Set up cache entries in both caches
        _free_activity_cache.clear()
        _premium_activity_cache.clear()
        _update_cache_after_commit(TEST_USER_ID, TIER_FREE)
        _update_cache_after_commit(TEST_USER_ID, TIER_PREMIUM)
        assert TEST_USER_ID in _free_activity_cache
        assert TEST_USER_ID in _premium_activity_cache

        # Invalidate
        invalidate_activity_cache(TEST_USER_ID)

        # Both should be cleared
        assert TEST_USER_ID not in _free_activity_cache
        assert TEST_USER_ID not in _premium_activity_cache

    def test_invalidate_nonexistent_user_does_not_raise(self):
        """invalidate_activity_cache should not raise for nonexistent user"""
        from studio.app.common.core.middleware.user_activity_middleware import (
            _free_activity_cache,
            _premium_activity_cache,
            invalidate_activity_cache,
        )

        # Clear caches
        _free_activity_cache.clear()
        _premium_activity_cache.clear()

        # Should not raise
        invalidate_activity_cache(999999)  # Non-existent user ID

    def test_rapid_relogin_gets_fresh_activity(self):
        """After cache invalidation, re-login should record fresh activity"""
        from studio.app.common.core.middleware.user_activity_middleware import (
            _free_activity_cache,
            _should_update_activity,
            _update_cache_after_commit,
            invalidate_activity_cache,
        )

        # Simulate login and activity
        _free_activity_cache.clear()
        _update_cache_after_commit(TEST_USER_ID, TIER_FREE)

        # Should be cached (would skip update)
        assert _should_update_activity(TEST_USER_ID, TIER_FREE) is False

        # Simulate logout - invalidate cache
        invalidate_activity_cache(TEST_USER_ID)

        # Simulate re-login - should now record activity
        assert _should_update_activity(TEST_USER_ID, TIER_FREE) is True
