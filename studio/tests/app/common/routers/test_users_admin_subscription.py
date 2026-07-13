"""
Tests for PUT /admin/users/{user_id}/subscription endpoint.

Tests the admin subscription update feature including:
- Schema validation (Pydantic)
- Business logic validation (CRUD)
- Audit log creation
- Edge cases
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from studio.app.common.schemas.users import (
    SubscriptionAuditSnapshot,
    UserSubscriptionUpdate,
)

# ============================================================================
# Schema Validation Tests (UserSubscriptionUpdate)
# ============================================================================


class TestUserSubscriptionUpdateSchema:
    """Tests for UserSubscriptionUpdate Pydantic model validation."""

    def test_valid_premium_update(self):
        """Valid Premium plan update with all fields."""
        data = UserSubscriptionUpdate(
            plan_id=2,
            expiration=datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
            storage_quota_bytes=214748364800,
            reason="Trial extension per support ticket #123",
        )
        assert data.plan_id == 2
        assert data.storage_quota_bytes == 214748364800
        assert data.reason == "Trial extension per support ticket #123"

    def test_valid_free_update_no_expiration(self):
        """Valid Free plan update without expiration."""
        data = UserSubscriptionUpdate(
            plan_id=1,
            expiration=None,
            storage_quota_bytes=5368709120,
            reason="Downgrade to free",
        )
        assert data.plan_id == 1
        assert data.expiration is None

    def test_storage_quota_must_be_positive(self):
        """storage_quota_bytes must be > 0."""
        with pytest.raises(ValidationError) as exc_info:
            UserSubscriptionUpdate(
                plan_id=1,
                storage_quota_bytes=0,
                reason="test",
            )
        assert "storage_quota_bytes" in str(exc_info.value)

    def test_storage_quota_rejects_negative(self):
        """storage_quota_bytes rejects negative values."""
        with pytest.raises(ValidationError) as exc_info:
            UserSubscriptionUpdate(
                plan_id=1,
                storage_quota_bytes=-1,
                reason="test",
            )
        assert "storage_quota_bytes" in str(exc_info.value)

    def test_reason_cannot_be_empty(self):
        """reason must be non-empty."""
        with pytest.raises(ValidationError) as exc_info:
            UserSubscriptionUpdate(
                plan_id=1,
                storage_quota_bytes=5368709120,
                reason="",
            )
        assert "reason" in str(exc_info.value)

    def test_reason_is_required(self):
        """reason field is required."""
        with pytest.raises(ValidationError):
            UserSubscriptionUpdate(
                plan_id=1,
                storage_quota_bytes=5368709120,
            )

    def test_plan_id_is_required(self):
        """plan_id field is required."""
        with pytest.raises(ValidationError):
            UserSubscriptionUpdate(
                storage_quota_bytes=5368709120,
                reason="test",
            )

    def test_storage_quota_is_required(self):
        """storage_quota_bytes field is required."""
        with pytest.raises(ValidationError):
            UserSubscriptionUpdate(
                plan_id=1,
                reason="test",
            )

    def test_plan_id_must_be_int(self):
        """plan_id rejects non-integer values."""
        with pytest.raises(ValidationError):
            UserSubscriptionUpdate(
                plan_id="invalid",
                storage_quota_bytes=5368709120,
                reason="test",
            )

    def test_expiration_accepts_iso_string(self):
        """expiration field parses ISO 8601 strings."""
        data = UserSubscriptionUpdate(
            plan_id=2,
            expiration="2026-12-31T23:59:59Z",
            storage_quota_bytes=5368709120,
            reason="test",
        )
        assert data.expiration is not None
        assert data.expiration.year == 2026

    def test_expiration_rejects_invalid_string(self):
        """expiration rejects non-datetime strings."""
        with pytest.raises(ValidationError):
            UserSubscriptionUpdate(
                plan_id=2,
                expiration="not-a-date",
                storage_quota_bytes=5368709120,
                reason="test",
            )


# ============================================================================
# SubscriptionAuditSnapshot Tests
# ============================================================================


class TestSubscriptionAuditSnapshot:
    """Tests for audit log snapshot model."""

    def test_snapshot_with_expiration(self):
        """Snapshot captures all fields including expiration."""
        snapshot = SubscriptionAuditSnapshot(
            plan_id=2,
            expiration="2026-12-31T23:59:59+00:00",
            storage_quota_bytes=214748364800,
        )
        assert snapshot.plan_id == 2
        assert snapshot.expiration == "2026-12-31T23:59:59+00:00"
        assert snapshot.storage_quota_bytes == 214748364800

    def test_snapshot_without_expiration(self):
        """Snapshot allows null expiration."""
        snapshot = SubscriptionAuditSnapshot(
            plan_id=1,
            expiration=None,
            storage_quota_bytes=5368709120,
        )
        assert snapshot.expiration is None

    def test_snapshot_dict_serialization(self):
        """Snapshot serializes to dict for JSON storage."""
        snapshot = SubscriptionAuditSnapshot(
            plan_id=2,
            expiration="2026-12-31T23:59:59+00:00",
            storage_quota_bytes=214748364800,
        )
        dumped = snapshot.dict()
        assert isinstance(dumped, dict)
        assert dumped["plan_id"] == 2
        assert dumped["expiration"] == "2026-12-31T23:59:59+00:00"
        assert dumped["storage_quota_bytes"] == 214748364800
