"""
Tests for Premium Assignment Rate Limit Persistence

Verifies that rate limit cache is NOT cleared on logout/release,
preventing rapid re-login attacks.
"""

import time
from unittest.mock import AsyncMock, patch

import pytest

from studio.app.common.core.premium.premium_assignment_service import (
    _RATE_LIMIT_SECONDS,
    PremiumAssignmentService,
    _assignment_attempts,
)


class TestRateLimitPersistence:
    """Test rate limit persistence across logout/release cycles"""

    def setup_method(self):
        """Clear cache before each test"""
        _assignment_attempts.clear()

    def teardown_method(self):
        """Clear cache after each test"""
        _assignment_attempts.clear()

    def test_can_assign_premium_returns_tuple(self):
        """Test can_assign_premium returns (can_assign, seconds_remaining)"""
        service = PremiumAssignmentService()

        result = service.can_assign_premium(user_id=1)

        assert isinstance(result, tuple)
        assert len(result) == 2
        can_assign, seconds_remaining = result
        assert isinstance(can_assign, bool)
        assert isinstance(seconds_remaining, int)

    def test_can_assign_premium_first_attempt(self):
        """Test first assignment attempt is allowed"""
        service = PremiumAssignmentService()

        can_assign, remaining = service.can_assign_premium(user_id=1)

        assert can_assign is True
        assert remaining == 0

    def test_can_assign_premium_rate_limited(self):
        """Test second attempt within rate limit window is blocked"""
        service = PremiumAssignmentService()

        # Record first attempt
        _assignment_attempts[1] = time.time()

        can_assign, remaining = service.can_assign_premium(user_id=1)

        assert can_assign is False
        assert remaining > 0
        assert remaining <= _RATE_LIMIT_SECONDS

    def test_can_assign_premium_after_expiry(self):
        """Test attempt after rate limit expires is allowed"""
        service = PremiumAssignmentService()

        # Record attempt in the past
        _assignment_attempts[1] = time.time() - _RATE_LIMIT_SECONDS - 1

        can_assign, remaining = service.can_assign_premium(user_id=1)

        assert can_assign is True
        assert remaining == 0

    @pytest.mark.asyncio
    async def test_rate_limit_not_cleared_on_release(self):
        """Test rate limit is NOT cleared when user releases instance"""
        service = PremiumAssignmentService()

        # Simulate an assignment attempt
        _assignment_attempts[1] = time.time()

        # Release should NOT clear rate limit
        with patch.object(service, "_get_lambda_client") as mock_client:
            mock_lambda = mock_client.return_value
            mock_lambda.invoke.return_value = {
                "Payload": AsyncMock(read=lambda: b'{"statusCode": 200, "body": "{}"}')
            }

            await service.release_premium_user(user_id=1, user_uid="test-uid")

        # Rate limit should still be in effect
        assert 1 in _assignment_attempts
        can_assign, _ = service.can_assign_premium(user_id=1)
        assert can_assign is False

    def test_rate_limit_returns_accurate_remaining_time(self):
        """Test remaining time is accurate"""
        service = PremiumAssignmentService()

        # Record attempt 10 seconds ago
        _assignment_attempts[1] = time.time() - 10

        can_assign, remaining = service.can_assign_premium(user_id=1)

        assert can_assign is False
        # Should be approximately 20 seconds remaining (30 - 10)
        assert 18 <= remaining <= 22

    @pytest.mark.asyncio
    async def test_assign_returns_retry_after_on_rate_limit(self):
        """Test assignment returns retry_after when rate limited"""
        service = PremiumAssignmentService()

        # Record recent attempt
        _assignment_attempts[1] = time.time()

        result = await service.assign_premium_user(user_id=1, user_uid="test")

        assert result["success"] is False
        assert "retry_after" in result
        assert result["retry_after"] > 0
        assert result["retry_after"] <= _RATE_LIMIT_SECONDS

    def test_different_users_independent_rate_limits(self):
        """Test rate limits are independent per user"""
        service = PremiumAssignmentService()

        # Rate limit user 1
        _assignment_attempts[1] = time.time()

        # User 1 should be blocked
        can_assign_1, _ = service.can_assign_premium(user_id=1)
        assert can_assign_1 is False

        # User 2 should not be affected
        can_assign_2, _ = service.can_assign_premium(user_id=2)
        assert can_assign_2 is True


class TestRateLimitCleanup:
    """Test rate limit cleanup logic"""

    def setup_method(self):
        """Clear cache before each test"""
        _assignment_attempts.clear()

    def teardown_method(self):
        """Clear cache after each test"""
        _assignment_attempts.clear()

    def test_cleanup_old_attempts(self):
        """Test old attempts are cleaned up"""
        service = PremiumAssignmentService()

        # Add old attempt (older than 2x rate limit)
        old_time = time.time() - (_RATE_LIMIT_SECONDS * 2 + 1)
        _assignment_attempts[1] = old_time

        # Add recent attempt
        _assignment_attempts[2] = time.time()

        service._cleanup_old_attempts()

        # Old attempt should be removed
        assert 1 not in _assignment_attempts
        # Recent attempt should remain
        assert 2 in _assignment_attempts

    def test_manual_clear_specific_user(self):
        """Test manual cache clearing for specific user"""
        service = PremiumAssignmentService()

        _assignment_attempts[1] = time.time()
        _assignment_attempts[2] = time.time()

        service.clear_rate_limit_cache(user_id=1)

        assert 1 not in _assignment_attempts
        assert 2 in _assignment_attempts

    def test_manual_clear_all(self):
        """Test manual cache clearing for all users"""
        service = PremiumAssignmentService()

        _assignment_attempts[1] = time.time()
        _assignment_attempts[2] = time.time()

        service.clear_rate_limit_cache()

        assert len(_assignment_attempts) == 0
