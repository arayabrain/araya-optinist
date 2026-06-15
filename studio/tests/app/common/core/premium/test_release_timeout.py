"""
Tests for the bounded timeout on PremiumAssignmentService.release_premium_user.

Regression coverage for issue #629 Problem 4: the release Lambda call must
not stall the caller (e.g. the Stripe webhook path) indefinitely. On timeout
it fails open (returns a dict, does not raise) so the caller can proceed; the
periodic premium-expiration sweep is the cleanup backstop.
"""

import time
from unittest.mock import AsyncMock, patch

import pytest

from studio.app.common.core.premium.premium_assignment_service import (
    PremiumAssignmentService,
)

_OK_PAYLOAD = {"Payload": AsyncMock(read=lambda: b'{"statusCode": 200, "body": "{}"}')}


class TestReleaseTimeout:
    @pytest.mark.asyncio
    async def test_release_times_out_fails_open(self):
        """A slow Lambda triggers the timeout and returns a fail-open result."""
        service = PremiumAssignmentService()

        def slow_invoke(*args, **kwargs):
            time.sleep(0.3)  # exceeds the 0.05s timeout below
            return _OK_PAYLOAD

        with patch.object(service, "_get_lambda_client") as mock_client:
            mock_client.return_value.invoke.side_effect = slow_invoke
            result = await service.release_premium_user(
                user_id=1, user_uid="uid", hard=True, timeout=0.05
            )

        assert result["success"] is False
        assert result["timed_out"] is True
        assert "timed out" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_release_succeeds_within_timeout(self):
        """The timeout parameter does not break the happy path."""
        service = PremiumAssignmentService()

        with patch.object(service, "_get_lambda_client") as mock_client:
            mock_client.return_value.invoke.return_value = _OK_PAYLOAD
            result = await service.release_premium_user(
                user_id=1, user_uid="uid", hard=True, timeout=5
            )

        assert result["success"] is True
        assert result.get("timed_out") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
