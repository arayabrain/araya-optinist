"""
Unit tests for SubscriptionService

Tests for:
- get_users_for_expiration_deletion()
"""

from datetime import timedelta
from unittest.mock import Mock

import pytest

from studio.app.common.core.subscription.subscription_service import SubscriptionService
from studio.app.common.core.utils.datetime_utils import get_current_datetime


class TestExpirationDeletion:
    """Expiration deletion eligibility tests."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session"""
        db = Mock()
        db.query = Mock()
        return db

    def test_no_users_for_expiration_deletion(self, mock_db):
        """Should return empty list when no users are eligible."""
        chain = mock_db.query.return_value.join.return_value.join.return_value
        chain.filter.return_value.order_by.return_value.all.return_value = []

        result = SubscriptionService.get_users_for_expiration_deletion(mock_db)

        assert result == []

    def test_finds_user_for_expiration_deletion(self, mock_db):
        """Should find users past grace period with storage over free quota."""
        from studio.app.common.core.subscription.constants import (
            ExpirationDeletion,
            SubscriptionPeriods,
        )

        current_time = get_current_datetime()

        mock_sub = Mock()
        mock_sub.expiration = current_time - timedelta(
            days=SubscriptionPeriods.GRACE_PERIOD_DAYS + 1
        )
        mock_sub.deletion_processed_at = None

        mock_user = Mock()
        mock_user.id = 123
        mock_user.uid = "user_uid_123"

        mock_storage = Mock()
        mock_storage.storage_usage_bytes = ExpirationDeletion.FREE_QUOTA_BYTES * 2

        chain = mock_db.query.return_value.join.return_value.join.return_value
        chain.filter.return_value.order_by.return_value.all.return_value = [
            (mock_sub, mock_user, mock_storage)
        ]

        result = SubscriptionService.get_users_for_expiration_deletion(mock_db)

        assert len(result) == 1
        assert result[0]["user_id"] == 123
        assert result[0]["excess_bytes"] > 0


class TestDetermineLifecycle:
    """SubscriptionService.determine_lifecycle() classification tests."""

    @staticmethod
    def _mock_db(expiration):
        db = Mock()
        subscription = Mock()
        subscription.expiration = expiration
        db.execute.return_value.all.return_value = [[subscription]]
        return db

    def test_none_expiration_returns_none(self):
        assert SubscriptionService.determine_lifecycle(self._mock_db(None), 1) is None

    def test_no_premium_rows_is_free(self):
        from studio.app.common.core.subscription.constants import (
            SubscriptionLifecycleStatus,
        )
        from studio.app.common.core.subscription.subscription_service import (
            SubscriptionLifecycle,
        )

        db = Mock()
        db.execute.return_value.all.return_value = []

        result = SubscriptionService.determine_lifecycle(db, 1)

        assert isinstance(result, SubscriptionLifecycle)
        assert result.status == SubscriptionLifecycleStatus.FREE


class TestGraceNotificationFlags:
    """Pure classification tests for grace notification emails."""

    @staticmethod
    def _flags(days_since_expiration, start_at=None, reminder_at=None):
        from studio.app.common.core.subscription.subscription_service import (
            SubscriptionService,
        )

        now = get_current_datetime()
        expiration = now - timedelta(days=days_since_expiration)
        return SubscriptionService.grace_notification_flags(
            expiration, start_at, reminder_at, now
        )

    def test_fresh_grace_user_needs_start_only(self):
        needs_start, needs_reminder = self._flags(days_since_expiration=1)
        assert needs_start is True
        assert needs_reminder is False

    def test_reminder_not_due_at_day_19(self):
        _, needs_reminder = self._flags(days_since_expiration=19)
        assert needs_reminder is False

    def test_reminder_due_at_day_20(self):
        _, needs_reminder = self._flags(days_since_expiration=20)
        assert needs_reminder is True

    def test_current_cycle_stamps_suppress_both(self):
        now = get_current_datetime()
        needs_start, needs_reminder = self._flags(
            days_since_expiration=21,
            start_at=now - timedelta(days=20),
            reminder_at=now - timedelta(days=1),
        )
        assert needs_start is False
        assert needs_reminder is False

    def test_stale_stamp_from_previous_cycle_resends(self):
        """Renewals reuse the subscription row: a stamp at or before the
        (new) expiration belongs to the previous cycle."""
        now = get_current_datetime()
        needs_start, needs_reminder = self._flags(
            days_since_expiration=21,
            start_at=now - timedelta(days=100),
            reminder_at=now - timedelta(days=100),
        )
        assert needs_start is True
        assert needs_reminder is True

    def test_naive_datetimes_accepted(self):
        from studio.app.common.core.subscription.subscription_service import (
            SubscriptionService,
        )

        now = get_current_datetime().replace(tzinfo=None)
        expiration = now - timedelta(days=25)
        needs_start, needs_reminder = SubscriptionService.grace_notification_flags(
            expiration, now - timedelta(days=24), None, now
        )
        assert needs_start is False
        assert needs_reminder is True


class TestClaimGraceNotification:
    """Claim/clear stamp tests (rowcount-based dedupe)."""

    @staticmethod
    def _mock_db(update_rowcount):
        db = Mock()
        db.query.return_value.filter.return_value.update.return_value = update_rowcount
        return db

    def test_claim_returns_stamp_when_row_updated(self):
        db = self._mock_db(update_rowcount=1)
        expiration = get_current_datetime() - timedelta(days=5)

        stamp = SubscriptionService.claim_grace_notification(db, 1, expiration, "start")

        assert stamp is not None
        assert stamp.microsecond == 0
        db.commit.assert_called_once()

    def test_claim_returns_none_when_already_claimed(self):
        db = self._mock_db(update_rowcount=0)
        expiration = get_current_datetime() - timedelta(days=5)

        stamp = SubscriptionService.claim_grace_notification(
            db, 1, expiration, "reminder"
        )

        assert stamp is None

    def test_clear_commits(self):
        db = self._mock_db(update_rowcount=1)
        SubscriptionService.clear_grace_notification(
            db, 1, "start", get_current_datetime()
        )
        db.commit.assert_called_once()


class TestGetUsersForGraceNotifications:
    """Query shaping tests (classification is covered by the flags tests)."""

    @staticmethod
    def _mock_db(rows):
        db = Mock()
        chain = db.query.return_value.join.return_value.join.return_value
        limited = chain.filter.return_value.order_by.return_value.limit.return_value
        limited.all.return_value = rows
        return db

    def test_no_rows_returns_empty(self):
        assert (
            SubscriptionService.get_users_for_grace_notifications(self._mock_db([]))
            == []
        )

    def test_returns_classified_users(self):
        now = get_current_datetime()

        sub = Mock()
        sub.expiration = now - timedelta(days=21)
        sub.grace_start_notified_at = now - timedelta(days=20)
        sub.grace_reminder_notified_at = None

        user = Mock()
        user.id = 7
        user.email = "user@example.org"

        storage = Mock()
        storage.storage_usage_bytes = 10 * 1024**3

        result = SubscriptionService.get_users_for_grace_notifications(
            self._mock_db([(sub, user, storage)])
        )

        assert len(result) == 1
        assert result[0]["user_id"] == 7
        assert result[0]["needs_start"] is False
        assert result[0]["needs_reminder"] is True
        assert result[0]["grace_end"] > now

    def test_fully_notified_row_is_dropped(self):
        now = get_current_datetime()

        sub = Mock()
        sub.expiration = now - timedelta(days=5)
        sub.grace_start_notified_at = now - timedelta(days=4)
        sub.grace_reminder_notified_at = None

        user = Mock()
        user.id = 7
        user.email = "user@example.org"

        storage = Mock()
        storage.storage_usage_bytes = 10 * 1024**3

        result = SubscriptionService.get_users_for_grace_notifications(
            self._mock_db([(sub, user, storage)])
        )

        assert result == []


class TestNotificationTimingInvariants:
    def test_reminder_and_notice_fit_inside_grace_period(self):
        from studio.app.common.core.subscription.constants import (
            ExpirationDeletion,
            SubscriptionPeriods,
        )

        assert (
            ExpirationDeletion.GRACE_REMINDER_AFTER_DAYS
            < SubscriptionPeriods.GRACE_PERIOD_DAYS
        )
        assert (
            ExpirationDeletion.MIN_NOTICE_DAYS <= SubscriptionPeriods.GRACE_PERIOD_DAYS
        )


class TestRequireNoticeFilter:
    """The warn-before-delete gate must be present in the SQL predicates."""

    @staticmethod
    def _mock_db():
        db = Mock()
        chain = db.query.return_value.join.return_value.join.return_value
        chain.filter.return_value.order_by.return_value.all.return_value = []
        return db, chain

    def test_default_has_no_notice_predicate(self):
        db, chain = self._mock_db()

        SubscriptionService.get_users_for_expiration_deletion(db)

        assert len(chain.filter.call_args_list) == 2
        for call in chain.filter.call_args_list:
            sql = " ".join(str(arg) for arg in call.args)
            assert "grace_start_notified_at" not in sql

    def test_require_notice_gates_on_warning_or_inactive(self):
        """Both eligibility queries must require a matured warning stamp,
        except for inactive users (who get no emails but must stay deletable)."""
        db, chain = self._mock_db()

        SubscriptionService.get_users_for_expiration_deletion(db, require_notice=True)

        assert len(chain.filter.call_args_list) == 2
        for call in chain.filter.call_args_list:
            sql = " ".join(str(arg) for arg in call.args)
            assert "grace_start_notified_at" in sql
            assert "users.active" in sql
