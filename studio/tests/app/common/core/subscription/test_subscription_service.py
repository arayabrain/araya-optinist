"""
Unit tests for SubscriptionService

Tests for:
- Case 79: get_users_with_upcoming_quota_drop()
"""

from datetime import timedelta
from unittest.mock import Mock, patch

import pytest

from studio.app.common.core.subscription.subscription_service import SubscriptionService
from studio.app.common.core.utils.datetime_utils import get_current_datetime


class TestQuotaDropWarning:
    """Test suite for Case 79: Quota drop warning."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session"""
        db = Mock()
        db.query = Mock()
        return db

    def test_no_users_with_upcoming_quota_drop(self, mock_db):
        """Should return empty list when no users have upcoming quota drops."""
        chain = mock_db.query.return_value.join.return_value.join.return_value
        chain.filter.return_value.all.return_value = []

        result = SubscriptionService.get_users_with_upcoming_quota_drop(mock_db)

        assert result == []

    def test_finds_user_with_quota_drop(self, mock_db):
        """Should find users whose storage exceeds free tier with grace ending soon."""
        from studio.app.common.core.subscription.constants import (
            StorageQuota,
            StorageSize,
            SubscriptionPeriods,
        )

        free_quota = StorageQuota.FREE * StorageSize.GB
        current_time = get_current_datetime()

        # Create mock subscription (expired 27 days ago, so 3 days until grace ends)
        mock_sub = Mock()
        mock_sub.expiration = current_time - timedelta(
            days=SubscriptionPeriods.GRACE_PERIOD_DAYS
            - SubscriptionPeriods.QUOTA_DROP_WARNING_DAYS
        )

        mock_user = Mock()
        mock_user.id = 123
        mock_user.uid = "user_uid_123"
        mock_user.email = "test@example.com"

        mock_storage = Mock()
        mock_storage.storage_usage_bytes = free_quota * 2  # Double the free quota

        chain = mock_db.query.return_value.join.return_value.join.return_value
        chain.filter.return_value.all.return_value = [
            (mock_sub, mock_user, mock_storage)
        ]

        with patch.object(
            SubscriptionService,
            "get_current_datetime",
            return_value=current_time,
        ):
            result = SubscriptionService.get_users_with_upcoming_quota_drop(mock_db)

        assert len(result) == 1
        assert result[0]["user_id"] == 123
        assert result[0]["excess_bytes"] > 0

    def test_quota_drop_warning_constant_is_3_days(self):
        """QUOTA_DROP_WARNING_DAYS should be 3 days."""
        from studio.app.common.core.subscription.constants import SubscriptionPeriods

        assert SubscriptionPeriods.QUOTA_DROP_WARNING_DAYS == 3
