"""
Integration tests for free user logout endpoint.

Tests logout tracking and cleanup scheduling.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from studio.app.common.core.subscription.constants import SubscriptionType
from studio.app.common.routers.users_me import logout_free_user


class TestLogoutFreeUser:
    """Test free user logout endpoint"""

    @pytest.mark.asyncio
    async def test_logout_free_user_success(self):
        mock_db = MagicMock()
        mock_free_user = MagicMock()
        mock_free_user.id = 123
        mock_free_user.subscription_type = SubscriptionType.FREE.value
        mock_assignment = MagicMock()
        mock_assignment.user_id = "123"
        mock_assignment.logged_out_at = None
        """Test successful logout for free tier user"""
        # Mock execute() to return a row-like tuple
        mock_db.execute.return_value.first.return_value = (mock_assignment,)

        result = await logout_free_user(current_user=mock_free_user, db=mock_db)

        assert result["logged_out"] is True
        assert result["cleanup_after_minutes"] == 60
        assert mock_assignment.logged_out_at is not None
        mock_db.add.assert_called_once_with(mock_assignment)
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_logout_premium_user_no_effect(self):
        mock_db = MagicMock()
        mock_premium_user = MagicMock()
        mock_premium_user.id = 456
        mock_premium_user.subscription_type = SubscriptionType.PREMIUM.value
        """Test logout for premium user has no effect"""
        result = await logout_free_user(current_user=mock_premium_user, db=mock_db)

        assert result["logged_out"] is False
        assert "only applies to free tier" in result["message"]
        mock_db.add.assert_not_called()
        mock_db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_logout_no_assignment_found(self):
        mock_db = MagicMock()
        mock_free_user = MagicMock()
        mock_free_user.id = 123
        mock_free_user.subscription_type = SubscriptionType.FREE.value
        """Test logout when user has no assignment"""
        # Mock execute() to return None
        mock_db.execute.return_value.first.return_value = None

        result = await logout_free_user(current_user=mock_free_user, db=mock_db)

        assert result["logged_out"] is True
        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_logout_updates_timestamp(self):
        mock_db = MagicMock()
        mock_free_user = MagicMock()
        mock_free_user.id = 123
        mock_free_user.subscription_type = SubscriptionType.FREE.value
        mock_assignment = MagicMock()
        mock_assignment.user_id = "123"
        mock_assignment.logged_out_at = None
        """Test that logout updates logged_out_at timestamp"""
        # Mock execute() to return a row-like tuple
        mock_db.execute.return_value.first.return_value = (mock_assignment,)

        before = datetime.now(timezone.utc)
        result = await logout_free_user(current_user=mock_free_user, db=mock_db)
        after = datetime.now(timezone.utc)

        assert result["logged_out"] is True
        assert mock_assignment.logged_out_at is not None
        assert before <= mock_assignment.logged_out_at <= after

    @pytest.mark.asyncio
    async def test_logout_handles_exception_gracefully(self):
        mock_db = MagicMock()
        mock_free_user = MagicMock()
        mock_free_user.id = 123
        mock_free_user.subscription_type = SubscriptionType.FREE.value
        mock_assignment = MagicMock()
        mock_assignment.user_id = "123"
        mock_assignment.logged_out_at = None
        """Test logout handles exceptions gracefully"""
        # Mock execute() to return a row-like tuple
        mock_db.execute.return_value.first.return_value = (mock_assignment,)
        mock_db.commit.side_effect = Exception("Database error")

        result = await logout_free_user(current_user=mock_free_user, db=mock_db)

        assert result["logged_out"] is False
        assert "error" in result
