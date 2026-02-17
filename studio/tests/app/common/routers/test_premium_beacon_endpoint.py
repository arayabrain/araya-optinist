"""Tests for Premium Release Beacon Endpoint"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from studio.app.common.routers.users_me import release_premium_beacon


class TestReleasePremiumBeacon:
    """Test beacon endpoint with token authentication"""

    @pytest.mark.asyncio
    async def test_beacon_missing_token(self):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={})
        mock_db = MagicMock()

        result = await release_premium_beacon(request=mock_request, db=mock_db)
        assert result["success"] is False
        assert "Missing token" in result["message"]

    @pytest.mark.asyncio
    async def test_beacon_invalid_token(self):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"token": "forged:123:abc"})
        mock_db = MagicMock()

        with patch(
            "studio.app.common.core.auth.security" ".validate_beacon_token",
            return_value=None,
        ):
            result = await release_premium_beacon(request=mock_request, db=mock_db)
        assert result["success"] is False
        assert "Invalid token" in result["message"]

    @pytest.mark.asyncio
    async def test_beacon_valid_token_user_not_found(self):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"token": "valid"})
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        with patch(
            "studio.app.common.core.auth.security" ".validate_beacon_token",
            return_value="uid-123",
        ):
            result = await release_premium_beacon(request=mock_request, db=mock_db)
        assert result["success"] is False
        assert "User not found" in result["message"]

    @pytest.mark.asyncio
    async def test_beacon_valid_token_success(self):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"token": "valid"})
        mock_user = MagicMock()
        mock_user.id = 42
        mock_user.uid = "uid-123"
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_user

        with patch(
            "studio.app.common.core.auth.security" ".validate_beacon_token",
            return_value="uid-123",
        ):
            with patch(
                "studio.app.common.routers.users_me" ".premium_assignment_service"
            ) as mock_svc:
                with patch(
                    "studio.app.common.routers.users_me" ".invalidate_activity_cache"
                ):
                    with patch(
                        "studio.app.common.routers.users_me" ".mark_user_logged_out"
                    ):
                        mock_svc.release_premium_user = AsyncMock(
                            return_value={
                                "success": True,
                                "message": "Released",
                            }
                        )
                        result = await release_premium_beacon(
                            request=mock_request,
                            db=mock_db,
                        )
        assert result["success"] is True
        mock_svc.release_premium_user.assert_called_once_with(
            user_id=42, user_uid="uid-123"
        )

    @pytest.mark.asyncio
    async def test_beacon_invalid_json(self):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(side_effect=json.JSONDecodeError("Bad", "", 0))
        mock_db = MagicMock()

        result = await release_premium_beacon(request=mock_request, db=mock_db)
        assert result["success"] is False
