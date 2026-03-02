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


class TestRateLimitCacheOnReleaseFailure:
    """Test rate limit cache preserved on release failure."""

    def setup_method(self):
        """Clear cache before each test"""
        _assignment_attempts.clear()

    def teardown_method(self):
        """Clear cache after each test"""
        _assignment_attempts.clear()

    @pytest.mark.asyncio
    async def test_cache_not_cleared_on_lambda_error(self):
        """Test rate limit cache is NOT cleared when Lambda call fails"""
        service = PremiumAssignmentService()

        # Simulate an assignment attempt
        _assignment_attempts[1] = time.time()

        # Simulate Lambda error
        with patch.object(service, "_get_lambda_client") as mock_client:
            mock_lambda = mock_client.return_value
            mock_lambda.invoke.side_effect = Exception("Lambda invocation failed")

            result = await service.release_premium_user(user_id=1, user_uid="test-uid")

        # Release should still return success (to not block logout)
        assert result["success"] is True
        assert "warnings" in result

        # Rate limit cache should still be in effect
        assert 1 in _assignment_attempts
        can_assign, _ = service.can_assign_premium(user_id=1)
        assert can_assign is False

    @pytest.mark.asyncio
    async def test_cache_not_cleared_on_non_200_response(self):
        """Test rate limit cache preserved on non-200 Lambda response"""
        service = PremiumAssignmentService()

        # Simulate an assignment attempt
        _assignment_attempts[1] = time.time()

        # Simulate non-200 response from Lambda
        with patch.object(service, "_get_lambda_client") as mock_client:
            mock_lambda = mock_client.return_value
            mock_response_payload = (
                b'{"statusCode": 500, "body": "{\\"error\\": \\"Internal error\\"}"}'
            )
            mock_lambda.invoke.return_value = {
                "Payload": AsyncMock(read=lambda: mock_response_payload)
            }

            result = await service.release_premium_user(user_id=1, user_uid="test-uid")

        # Release should still return success (to not block logout)
        assert result["success"] is True

        # Rate limit cache should still be in effect
        assert 1 in _assignment_attempts
        can_assign, _ = service.can_assign_premium(user_id=1)
        assert can_assign is False

    @pytest.mark.asyncio
    async def test_cache_not_cleared_on_partial_success(self):
        """Test rate limit cache preserved even on successful release"""
        service = PremiumAssignmentService()

        # Simulate an assignment attempt
        _assignment_attempts[1] = time.time()

        # Simulate successful release response
        with patch.object(service, "_get_lambda_client") as mock_client:
            mock_lambda = mock_client.return_value
            mock_response_payload = (
                b'{"statusCode": 200, "body": "{\\"success\\": true, '
                b'\\"message\\": \\"Released\\"}"}'
            )
            mock_lambda.invoke.return_value = {
                "Payload": AsyncMock(read=lambda: mock_response_payload)
            }

            await service.release_premium_user(user_id=1, user_uid="test-uid")

        # Rate limit cache should STILL be in effect after release failure.
        # This prevents rapid re-login attempts
        assert 1 in _assignment_attempts
        can_assign, _ = service.can_assign_premium(user_id=1)
        assert can_assign is False


class TestRateLimitRegressions:
    """Regression tests to prevent rate limit bypass bugs"""

    def setup_method(self):
        """Clear cache before each test"""
        _assignment_attempts.clear()

    def teardown_method(self):
        """Clear cache after each test"""
        _assignment_attempts.clear()

    def test_rate_limit_enforced_at_30_seconds_not_shorter(self):
        """
        Regression test: Rate limit must be 30 seconds, not shorter.

        This prevents bugs where debug code or other changes reduce
        the effective rate limit window (e.g., clearing after 5 seconds).
        """
        service = PremiumAssignmentService()

        # Record attempt at 10 seconds ago (well under 30s limit)
        _assignment_attempts[1] = time.time() - 10

        # Should still be rate limited
        can_assign, remaining = service.can_assign_premium(user_id=1)

        assert (
            can_assign is False
        ), "Rate limit should be enforced at 30 seconds, not shorter"
        assert remaining > 15, (
            f"Expected >15s remaining but got {remaining}s - "
            "rate limit window may have been reduced"
        )

    def test_rate_limit_enforced_at_25_seconds(self):
        """Verify rate limit is still active at 25 seconds"""
        service = PremiumAssignmentService()

        # Record attempt at 25 seconds ago
        _assignment_attempts[1] = time.time() - 25

        can_assign, remaining = service.can_assign_premium(user_id=1)

        assert can_assign is False, "Rate limit should still be active at 25 seconds"
        assert 3 <= remaining <= 7, f"Expected ~5s remaining but got {remaining}s"

    def test_rate_limit_expires_after_full_30_seconds(self):
        """Verify rate limit expires after full 30 seconds"""
        service = PremiumAssignmentService()

        # Record attempt at 31 seconds ago (just past the 30s limit)
        _assignment_attempts[1] = time.time() - 31

        can_assign, remaining = service.can_assign_premium(user_id=1)

        assert can_assign is True, "Rate limit should expire after 30 seconds"
        assert remaining == 0, "No time remaining after rate limit expires"

    def test_rate_limit_constant_is_30_seconds(self):
        """Verify the rate limit constant hasn't been changed"""
        assert (
            _RATE_LIMIT_SECONDS == 30
        ), f"Rate limit constant should be 30 seconds, not {_RATE_LIMIT_SECONDS}"


class TestLambdaTimeoutHandling:
    """Tests for Lambda timeout and retry handling."""

    def setup_method(self):
        """Clear cache before each test"""
        _assignment_attempts.clear()

    def teardown_method(self):
        """Clear cache after each test"""
        _assignment_attempts.clear()

    def test_timeout_constants_defined(self):
        """Verify timeout constants are properly defined."""
        from studio.app.common.core.premium.premium_assignment_service import (
            LAMBDA_MAX_RETRIES,
            LAMBDA_RETRY_BASE_DELAY_SECONDS,
            LAMBDA_TIMEOUT_SECONDS,
        )

        assert LAMBDA_TIMEOUT_SECONDS == 60
        assert LAMBDA_MAX_RETRIES == 2
        assert LAMBDA_RETRY_BASE_DELAY_SECONDS == 2

    @pytest.mark.asyncio
    async def test_timeout_returns_requires_retry(self):
        """Timeout should return requires_retry=True with retry_after."""
        service = PremiumAssignmentService()

        def slow_invoke(*args, **kwargs):
            # Simulate slow Lambda that will timeout
            time.sleep(0.5)
            return {"Payload": AsyncMock(read=lambda: b"{}")}

        # Mock is_local_environment to return False so Lambda path is taken
        with patch(
            "studio.app.common.core.premium.premium_assignment_service."
            "is_local_environment",
            return_value=False,
        ):
            with patch.object(service, "_get_lambda_client") as mock_client:
                mock_lambda = mock_client.return_value
                mock_lambda.invoke.side_effect = slow_invoke

                # Reduce timeout for test speed
                with patch(
                    "studio.app.common.core.premium.premium_assignment_service."
                    "LAMBDA_TIMEOUT_SECONDS",
                    0.1,
                ):
                    with patch(
                        "studio.app.common.core.premium.premium_assignment_service."
                        "LAMBDA_RETRY_BASE_DELAY_SECONDS",
                        0.01,
                    ):
                        result = await service.assign_premium_user(
                            user_id=1, user_uid="test-uid"
                        )

        assert result["success"] is False
        assert result["requires_retry"] is True
        assert "retry_after" in result
        assert "timed out" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_successful_assignment_after_retry(self):
        """Should succeed after transient error on retry."""
        service = PremiumAssignmentService()
        call_count = 0

        def invoke_with_retry(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call fails
                raise Exception("Transient error")
            # Subsequent calls succeed
            return {
                "Payload": AsyncMock(
                    read=lambda: b'{"statusCode": 200, "body": "{\\"success\\": true, '
                    b'\\"instance_id\\": \\"test-instance\\"}"}'
                )
            }

        with patch(
            "studio.app.common.core.premium.premium_assignment_service."
            "is_local_environment",
            return_value=False,
        ):
            with patch.object(service, "_get_lambda_client") as mock_client:
                mock_lambda = mock_client.return_value
                mock_lambda.invoke.side_effect = invoke_with_retry

                with patch(
                    "studio.app.common.core.premium.premium_assignment_service."
                    "LAMBDA_RETRY_BASE_DELAY_SECONDS",
                    0.01,
                ):
                    result = await service.assign_premium_user(
                        user_id=1, user_uid="test-uid"
                    )

        # Should succeed on retry
        assert result["success"] is True
        assert call_count == 2  # First fails, second succeeds
