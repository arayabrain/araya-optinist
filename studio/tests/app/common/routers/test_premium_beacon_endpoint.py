"""
Tests for Premium Release Beacon Endpoint

Tests the /premium/release-beacon endpoint used for reliable cleanup
when browser closes or page refreshes.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from studio.app.common.routers.users_me import release_premium_beacon


class TestReleasePremiumBeacon:
    """Test beacon endpoint for browser close cleanup"""

    @pytest.mark.asyncio
    async def test_beacon_release_success(self):
        """Test successful beacon release with valid user_uid"""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"user_uid": "test-user-123"})

        with patch(
            "studio.app.common.routers.users_me.premium_assignment_service"
        ) as mock_service:
            mock_service.release_premium_user = AsyncMock(
                return_value={
                    "success": True,
                    "message": "Released from instance i-123456",
                }
            )

            result = await release_premium_beacon(request=mock_request)

            assert result["success"] is True
            mock_service.release_premium_user.assert_called_once_with(
                user_id=0, user_uid="test-user-123"
            )

    @pytest.mark.asyncio
    async def test_beacon_release_missing_user_uid(self):
        """Test beacon release with missing user_uid returns failure"""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={})

        result = await release_premium_beacon(request=mock_request)

        assert result["success"] is False
        assert "Missing user_uid" in result["message"]

    @pytest.mark.asyncio
    async def test_beacon_release_null_user_uid(self):
        """Test beacon release with null user_uid returns failure"""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"user_uid": None})

        result = await release_premium_beacon(request=mock_request)

        assert result["success"] is False
        assert "Missing user_uid" in result["message"]

    @pytest.mark.asyncio
    async def test_beacon_release_service_error(self):
        """Test beacon release handles service errors gracefully"""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"user_uid": "test-user-123"})

        with patch(
            "studio.app.common.routers.users_me.premium_assignment_service"
        ) as mock_service:
            mock_service.release_premium_user = AsyncMock(
                side_effect=Exception("Lambda timeout")
            )

            result = await release_premium_beacon(request=mock_request)

            # Should not raise, just return failure
            assert result["success"] is False
            assert "Lambda timeout" in result["message"]

    @pytest.mark.asyncio
    async def test_beacon_release_invalid_json(self):
        """Test beacon release handles invalid JSON gracefully"""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            side_effect=json.JSONDecodeError("Invalid", "", 0)
        )

        result = await release_premium_beacon(request=mock_request)

        # Should not raise, just return failure
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_beacon_does_not_require_auth(self):
        """Test beacon endpoint does not require authentication"""
        # The endpoint signature should not have Depends(get_current_user)
        import inspect

        sig = inspect.signature(release_premium_beacon)
        params = list(sig.parameters.keys())

        # Should only have 'request' parameter, not 'current_user'
        assert "request" in params
        assert "current_user" not in params
