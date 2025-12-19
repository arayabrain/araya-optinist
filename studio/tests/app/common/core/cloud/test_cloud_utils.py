"""
Unit tests for cloud_utils.py

Tests cover:
- calculate_limit_warning() - 5 warning cases with subscription lifecycle states
- _is_storage_data_fresh() - Date parsing and timezone handling
- get_current_user_storage_usage() - Hybrid caching logic
- _get_fallback_storage_quota() - Subscription plan determination
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest

from studio.app.common.core.cloud.cloud_utils import (
    _get_fallback_storage_quota,
    _is_storage_data_fresh,
    calculate_limit_warning,
    get_current_user_storage_usage,
)
from studio.app.common.core.subscription.constants import (
    PlanName,
    StorageQuota,
    StorageSize,
    SubscriptionPeriods,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_db():
    """Mock database session"""
    db = Mock()
    db.execute = Mock()
    db.add = Mock()
    db.commit = Mock()
    return db


@pytest.fixture
def mock_subscription_free():
    """Mock free plan subscription result"""
    result = Mock()
    result.plan_name = PlanName.FREE
    return result


@pytest.fixture
def mock_subscription_premium():
    """Mock premium plan subscription result"""
    result = Mock()
    result.plan_name = PlanName.PREMIUM
    return result


@pytest.fixture
def mock_storage_info_fresh():
    """Mock storage info with fresh timestamp (within 20 minutes)"""
    return {
        "user_id": 1,
        "storage_usage_bytes": 1_000_000_000,  # 1 GB
        "storage_quota_bytes": 5_000_000_000,  # 5 GB
        "storage_usage_percent": 20.0,
        "last_updated": datetime.now(timezone.utc) - timedelta(minutes=10),
    }


@pytest.fixture
def mock_storage_info_stale():
    """Mock storage info with stale timestamp (over 20 minutes old)"""
    return {
        "user_id": 1,
        "storage_usage_bytes": 1_000_000_000,
        "storage_quota_bytes": 5_000_000_000,
        "storage_usage_percent": 20.0,
        "last_updated": datetime.now(timezone.utc) - timedelta(minutes=30),
    }


# ============================================================================
# Tests for _get_fallback_storage_quota()
# ============================================================================


def test_get_fallback_storage_quota_free_plan():
    """Test fallback quota for free plan user"""
    user_id = 1

    with patch("studio.app.common.core.cloud.cloud_utils.session_scope") as mock_scope:
        mock_db = Mock()
        mock_scope.return_value.__enter__.return_value = mock_db

        # Mock query result for free plan
        mock_result = Mock()
        mock_result.plan_name = PlanName.FREE
        mock_db.execute.return_value.first.return_value = mock_result

        result = _get_fallback_storage_quota(user_id)

        assert result["user_id"] == user_id
        assert result["storage_quota_bytes"] == StorageQuota.FREE * StorageSize.GB
        assert result["storage_usage_bytes"] == 0
        assert result["storage_usage_percent"] == 0.0
        assert result["last_updated"] is None


def test_get_fallback_storage_quota_premium_plan():
    """Test fallback quota for premium plan user"""
    user_id = 2

    with patch("studio.app.common.core.cloud.cloud_utils.session_scope") as mock_scope:
        mock_db = Mock()
        mock_scope.return_value.__enter__.return_value = mock_db

        # Mock query result for premium plan
        mock_result = Mock()
        mock_result.plan_name = PlanName.PREMIUM
        mock_db.execute.return_value.first.return_value = mock_result

        result = _get_fallback_storage_quota(user_id)

        assert result["user_id"] == user_id
        assert result["storage_quota_bytes"] == StorageQuota.PREMIUM * StorageSize.GB
        assert result["storage_usage_bytes"] == 0


def test_get_fallback_storage_quota_no_subscription():
    """Test fallback quota when user has no subscription"""
    user_id = 3

    with patch("studio.app.common.core.cloud.cloud_utils.session_scope") as mock_scope:
        mock_db = Mock()
        mock_scope.return_value.__enter__.return_value = mock_db

        # Mock query result with no plan
        mock_db.execute.return_value.first.return_value = None

        result = _get_fallback_storage_quota(user_id)

        # Should default to free plan
        assert result["storage_quota_bytes"] == StorageQuota.FREE * StorageSize.GB


def test_get_fallback_storage_quota_database_error():
    """Test fallback quota when database error occurs"""
    user_id = 4

    with patch("studio.app.common.core.cloud.cloud_utils.session_scope") as mock_scope:
        mock_scope.side_effect = Exception("Database connection failed")

        result = _get_fallback_storage_quota(user_id)

        # Should fallback to free plan
        assert result["storage_quota_bytes"] == StorageQuota.FREE * StorageSize.GB


# ============================================================================
# Tests for _is_storage_data_fresh()
# ============================================================================


def test_is_storage_data_fresh_within_cache_window():
    """Test that fresh data (within cache window) returns True"""
    storage_info = {"last_updated": datetime.now(timezone.utc) - timedelta(minutes=10)}

    result = _is_storage_data_fresh(
        storage_info, SubscriptionPeriods.MAX_CACHE_AGE_MINUTES
    )

    assert result is True


def test_is_storage_data_fresh_outside_cache_window():
    """Test that stale data (outside cache window) returns False"""
    storage_info = {"last_updated": datetime.now(timezone.utc) - timedelta(minutes=30)}

    result = _is_storage_data_fresh(
        storage_info, SubscriptionPeriods.MAX_CACHE_AGE_MINUTES
    )

    assert result is False


def test_is_storage_data_fresh_missing_last_updated():
    """Test that missing last_updated field returns False"""
    storage_info = {"storage_usage_bytes": 1000}

    result = _is_storage_data_fresh(
        storage_info, SubscriptionPeriods.MAX_CACHE_AGE_MINUTES
    )

    assert result is False


def test_is_storage_data_fresh_string_format():
    """Test that ISO string format timestamps work correctly"""
    # Create timestamp as ISO string
    timestamp = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    storage_info = {"last_updated": timestamp}

    result = _is_storage_data_fresh(
        storage_info, SubscriptionPeriods.MAX_CACHE_AGE_MINUTES
    )

    assert result is True


def test_is_storage_data_fresh_string_format_with_z():
    """Test that ISO string with 'Z' suffix (UTC) works correctly"""
    # Create timestamp with Z suffix (common in JSON APIs)
    timestamp = (
        (datetime.now(timezone.utc) - timedelta(minutes=5))
        .isoformat()
        .replace("+00:00", "Z")
    )
    storage_info = {"last_updated": timestamp}

    result = _is_storage_data_fresh(
        storage_info, SubscriptionPeriods.MAX_CACHE_AGE_MINUTES
    )

    assert result is True


def test_is_storage_data_fresh_invalid_string_format():
    """Test that invalid date string returns False"""
    storage_info = {"last_updated": "invalid-date-string"}

    result = _is_storage_data_fresh(
        storage_info, SubscriptionPeriods.MAX_CACHE_AGE_MINUTES
    )

    assert result is False


def test_is_storage_data_fresh_exactly_at_boundary():
    """Test boundary condition: exactly at max_cache_age_minutes"""
    storage_info = {
        "last_updated": datetime.now(timezone.utc)
        - timedelta(minutes=SubscriptionPeriods.MAX_CACHE_AGE_MINUTES)
    }

    result = _is_storage_data_fresh(
        storage_info, SubscriptionPeriods.MAX_CACHE_AGE_MINUTES
    )

    # Implementation uses < (not <=), so data exactly at boundary is stale
    assert result is False


# ============================================================================
# Tests for get_current_user_storage_usage()
# ============================================================================


@pytest.mark.asyncio
async def test_get_current_user_storage_usage_fresh_cache_hit(
    mock_storage_info_fresh,
):
    """Test that fresh cached data is returned without recalculation"""
    user_id = 1

    with patch(
        "studio.app.common.core.cloud.cloud_utils.get_user_storage_usage"
    ) as mock_get_storage:
        mock_get_storage.return_value = mock_storage_info_fresh

        result = await get_current_user_storage_usage(user_id, force_live=False)

        assert result == mock_storage_info_fresh["storage_usage_bytes"]
        # Should not call live calculation
        mock_get_storage.assert_called_once_with(user_id)


@pytest.mark.asyncio
async def test_get_current_user_storage_usage_stale_cache_recalculates(
    mock_storage_info_stale,
):
    """Test that stale cached data triggers recalculation"""
    user_id = 1
    live_usage = 2_000_000_000  # 2 GB

    with patch(
        "studio.app.common.core.cloud.cloud_utils.get_user_storage_usage"
    ) as mock_get_storage:
        with patch(
            "studio.app.common.core.cloud.cloud_utils._calculate_live_storage_usage"
        ) as mock_live_calc:
            with patch(
                "studio.app.common.core.cloud.cloud_utils.update_user_storage_usage"
            ) as mock_update:
                mock_get_storage.return_value = mock_storage_info_stale
                mock_live_calc.return_value = live_usage

                result = await get_current_user_storage_usage(user_id, force_live=False)

                assert result == live_usage
                mock_live_calc.assert_called_once_with(user_id)
                mock_update.assert_called_once_with(user_id, live_usage)


@pytest.mark.asyncio
async def test_get_current_user_storage_usage_force_live():
    """Test that force_live=True always calculates live usage"""
    user_id = 1
    live_usage = 3_000_000_000

    with patch(
        "studio.app.common.core.cloud.cloud_utils._calculate_live_storage_usage"
    ) as mock_live_calc:
        with patch(
            "studio.app.common.core.cloud.cloud_utils.update_user_storage_usage"
        ):
            mock_live_calc.return_value = live_usage

            result = await get_current_user_storage_usage(user_id, force_live=True)

            assert result == live_usage
            mock_live_calc.assert_called_once_with(user_id)


@pytest.mark.asyncio
async def test_get_current_user_storage_usage_calculation_fails_fallback():
    """Test fallback to database when live calculation fails"""
    user_id = 1
    cached_usage = 1_000_000_000

    with patch(
        "studio.app.common.core.cloud.cloud_utils.get_user_storage_usage"
    ) as mock_get_storage:
        with patch(
            "studio.app.common.core.cloud.cloud_utils._calculate_live_storage_usage"
        ) as mock_live_calc:
            mock_get_storage.return_value = {"storage_usage_bytes": cached_usage}
            mock_live_calc.side_effect = Exception("S3 connection failed")

            result = await get_current_user_storage_usage(user_id, force_live=True)

            # Should fallback to cached value
            assert result == cached_usage


# ============================================================================
# Tests for calculate_limit_warning()
# ============================================================================


@pytest.mark.asyncio
async def test_calculate_limit_warning_free_user_no_warning():
    """
    Case 1: Free user, no storage limit exceeded → No warning
    """
    user_id = 1

    with patch("studio.app.common.core.cloud.cloud_utils.session_scope") as mock_scope:
        with patch(
            "studio.app.common.core.cloud.cloud_utils.get_user_storage_usage"
        ) as mock_get_storage:
            with patch(
                "studio.app.common.core.cloud.cloud_utils._is_storage_data_fresh"
            ) as mock_fresh:
                mock_db = Mock()
                mock_scope.return_value.__enter__.return_value = mock_db

                # Mock storage info: 2GB used of 5GB (40% - under limit)
                mock_get_storage.return_value = {
                    "storage_usage_bytes": 2_000_000_000,
                    "storage_quota_bytes": 5_000_000_000,
                }
                mock_fresh.return_value = True

                # Mock no subscription (free user)
                mock_db.execute.return_value.all.return_value = []

                result = await calculate_limit_warning(user_id)

                assert result is None  # No warning for free user within limits


@pytest.mark.asyncio
async def test_calculate_limit_warning_free_user_storage_exceeded():
    """
    Case 2: Free user, storage limit exceeded → Storage warning
    """
    user_id = 1
    excess_bytes = 750_000_000  # 0.75 GB excess

    with patch("studio.app.common.core.cloud.cloud_utils.session_scope") as mock_scope:
        with patch(
            "studio.app.common.core.cloud.cloud_utils.get_user_storage_usage"
        ) as mock_get_storage:
            with patch(
                "studio.app.common.core.cloud.cloud_utils._is_storage_data_fresh"
            ) as mock_fresh:
                mock_db = Mock()
                mock_scope.return_value.__enter__.return_value = mock_db

                # Mock storage info: 5.75GB used of 5GB (115% - over limit)
                mock_get_storage.return_value = {
                    "storage_usage_bytes": 5_750_000_000,
                    "storage_quota_bytes": 5_000_000_000,
                }
                mock_fresh.return_value = True

                # Mock no subscription (free user)
                mock_db.execute.return_value.all.return_value = []

                result = await calculate_limit_warning(user_id)

                assert result is not None
                assert result["has_warning"] is True
                assert result["warning_type"] == "storage"
                assert (
                    result["days_remaining"] == SubscriptionPeriods.STORAGE_WARNING_DAYS
                )
                assert result["excess_data_bytes"] == excess_bytes
                assert "exceeds the free plan limit" in result["message"]


@pytest.mark.asyncio
async def test_calculate_limit_warning_premium_active_storage_exceeded():
    """
    Case 3: Premium user (active), storage limit exceeded → Storage warning only
    """
    user_id = 1

    with patch("studio.app.common.core.cloud.cloud_utils.session_scope") as mock_scope:
        with patch(
            "studio.app.common.core.cloud.cloud_utils.get_user_storage_usage"
        ) as mock_get_storage:
            with patch(
                "studio.app.common.core.cloud.cloud_utils._is_storage_data_fresh"
            ) as mock_fresh:
                mock_db = Mock()
                mock_scope.return_value.__enter__.return_value = mock_db

                # Mock storage info: 205GB used of 200GB (over premium limit)
                mock_get_storage.return_value = {
                    "storage_usage_bytes": 205_000_000_000,
                    "storage_quota_bytes": 200_000_000_000,
                }
                mock_fresh.return_value = True

                # Mock active premium subscription (expires in future)
                mock_subscription = Mock()
                mock_subscription.expiration = datetime.now(timezone.utc) + timedelta(
                    days=30
                )
                mock_db.execute.return_value.all.return_value = [[mock_subscription]]

                result = await calculate_limit_warning(user_id)

                assert result is not None
                assert result["has_warning"] is True
                assert result["warning_type"] == "storage"
                assert (
                    result["days_remaining"] == SubscriptionPeriods.STORAGE_WARNING_DAYS
                )
                assert "unable to run workflows" in result["message"]


@pytest.mark.asyncio
async def test_calculate_limit_warning_premium_warning_storage_ok():
    """
    Case 4: Premium user in WARNING period, storage OK → Subscription warning only
    Note: After grace period expires, user falls back to FREE quota (5GB)
    """
    user_id = 1
    grace_period = SubscriptionPeriods.GRACE_PERIOD_DAYS
    warning_period = SubscriptionPeriods.WARNING_PERIOD_DAYS

    with patch("studio.app.common.core.cloud.cloud_utils.session_scope") as mock_scope:
        with patch(
            "studio.app.common.core.cloud.cloud_utils.get_user_storage_usage"
        ) as mock_get_storage:
            with patch(
                "studio.app.common.core.cloud.cloud_utils._is_storage_data_fresh"
            ) as mock_fresh:
                mock_db = Mock()
                mock_scope.return_value.__enter__.return_value = mock_db

                # Mock storage info: 2GB used of 5GB FREE quota (within limits)
                # After expiration, user falls back to FREE quota
                mock_get_storage.return_value = {
                    "storage_usage_bytes": 2_000_000_000,  # 2 GB
                    "storage_quota_bytes": 5_000_000_000,  # 5 GB (FREE quota)
                }
                mock_fresh.return_value = True

                # Mock expired subscription in WARNING period
                # Expired 10 days ago, so in warning period (0-30 days after grace)
                expiration_date = datetime.now(timezone.utc) - timedelta(
                    days=grace_period + 10
                )
                deletion_date = expiration_date + timedelta(
                    days=grace_period + warning_period
                )

                mock_subscription = Mock()
                mock_subscription.expiration = expiration_date
                mock_db.execute.return_value.all.return_value = [[mock_subscription]]

                result = await calculate_limit_warning(user_id)

                assert result is not None
                assert result["has_warning"] is True
                assert result["warning_type"] == "grace"
                # days_remaining is (deletion_date - now).days
                # Expected: warning_period - 10 days remaining until deletion
                expected_days = (deletion_date - datetime.now(timezone.utc)).days
                assert (
                    result["days_remaining"] >= expected_days - 1
                )  # Allow 1 day variance for test timing
                assert result["days_remaining"] <= expected_days + 1
                assert "expired" in result["message"]
                assert "upgrade to maintain premium features" in result["message"]


@pytest.mark.asyncio
async def test_calculate_limit_warning_premium_warning_storage_exceeded():
    """
    Case 5: Premium user in WARNING period, storage exceeded → Combined warning
    Note: After grace period expires, user falls back to FREE quota (5GB)
    """
    user_id = 1
    grace_period = SubscriptionPeriods.GRACE_PERIOD_DAYS
    warning_period = SubscriptionPeriods.WARNING_PERIOD_DAYS

    with patch("studio.app.common.core.cloud.cloud_utils.session_scope") as mock_scope:
        with patch(
            "studio.app.common.core.cloud.cloud_utils.get_user_storage_usage"
        ) as mock_get_storage:
            with patch(
                "studio.app.common.core.cloud.cloud_utils._is_storage_data_fresh"
            ) as mock_fresh:
                mock_db = Mock()
                mock_scope.return_value.__enter__.return_value = mock_db

                # Mock storage info: 8GB used of 5GB (over FREE limit after downgrade)
                mock_get_storage.return_value = {
                    "storage_usage_bytes": 8_000_000_000,
                    "storage_quota_bytes": 5_000_000_000,
                }
                mock_fresh.return_value = True

                # Mock expired subscription in WARNING period
                expiration_date = datetime.now(timezone.utc) - timedelta(
                    days=grace_period + 5
                )
                deletion_date = expiration_date + timedelta(
                    days=grace_period + warning_period
                )

                mock_subscription = Mock()
                mock_subscription.expiration = expiration_date
                mock_db.execute.return_value.all.return_value = [[mock_subscription]]

                result = await calculate_limit_warning(user_id)

                assert result is not None
                assert result["has_warning"] is True
                assert result["warning_type"] == "grace"
                # days_remaining is (deletion_date - now).days
                expected_days = (deletion_date - datetime.now(timezone.utc)).days
                assert (
                    result["days_remaining"] >= expected_days - 1
                )  # Allow 1 day variance
                assert result["days_remaining"] <= expected_days + 1
                assert "expired" in result["message"]
                assert "remove" in result["message"] or "upgrade" in result["message"]
                # Verify excess is approximately 3GB (8GB - 5GB)
                # Allow for rounding: round((8_000_000_000 - 5_000_000_000) / GB, 2)
                assert result["excess_data_gb"] >= 2.7
                assert result["excess_data_gb"] <= 3.0


@pytest.mark.asyncio
async def test_calculate_limit_warning_premium_overdue():
    """
    Test: Premium user OVERDUE (past deletion date) → Overdue warning
    """
    user_id = 1
    grace_period = SubscriptionPeriods.GRACE_PERIOD_DAYS
    warning_period = SubscriptionPeriods.WARNING_PERIOD_DAYS

    with patch("studio.app.common.core.cloud.cloud_utils.session_scope") as mock_scope:
        with patch(
            "studio.app.common.core.cloud.cloud_utils.get_user_storage_usage"
        ) as mock_get_storage:
            with patch(
                "studio.app.common.core.cloud.cloud_utils._is_storage_data_fresh"
            ) as mock_fresh:
                mock_db = Mock()
                mock_scope.return_value.__enter__.return_value = mock_db

                # Mock storage info
                mock_get_storage.return_value = {
                    "storage_usage_bytes": 6_000_000_000,
                    "storage_quota_bytes": 5_000_000_000,
                }
                mock_fresh.return_value = True

                # Mock expired subscription past deletion date
                expiration_date = datetime.now(timezone.utc) - timedelta(
                    days=grace_period + warning_period + 5
                )
                mock_subscription = Mock()
                mock_subscription.expiration = expiration_date
                mock_db.execute.return_value.all.return_value = [[mock_subscription]]

                result = await calculate_limit_warning(user_id)

                assert result is not None
                assert result["has_warning"] is True
                assert result["warning_type"] == "overdue"
                assert result["days_remaining"] == 0


@pytest.mark.asyncio
async def test_calculate_limit_warning_premium_active_no_storage_issue():
    """
    Test: Premium user active with no storage issue → No warning
    """
    user_id = 1

    with patch("studio.app.common.core.cloud.cloud_utils.session_scope") as mock_scope:
        with patch(
            "studio.app.common.core.cloud.cloud_utils.get_user_storage_usage"
        ) as mock_get_storage:
            with patch(
                "studio.app.common.core.cloud.cloud_utils._is_storage_data_fresh"
            ) as mock_fresh:
                mock_db = Mock()
                mock_scope.return_value.__enter__.return_value = mock_db

                # Mock storage info: 50GB used of 200GB (within limits)
                mock_get_storage.return_value = {
                    "storage_usage_bytes": 50_000_000_000,
                    "storage_quota_bytes": 200_000_000_000,
                }
                mock_fresh.return_value = True

                # Mock active premium subscription
                mock_subscription = Mock()
                mock_subscription.expiration = datetime.now(timezone.utc) + timedelta(
                    days=30
                )
                mock_db.execute.return_value.all.return_value = [[mock_subscription]]

                result = await calculate_limit_warning(user_id)

                assert result is None  # No warning


@pytest.mark.asyncio
async def test_calculate_limit_warning_premium_in_grace_period():
    """
    Test: Premium user in GRACE period (0-7 days after expiration) → No warning yet
    """
    user_id = 1

    with patch("studio.app.common.core.cloud.cloud_utils.session_scope") as mock_scope:
        with patch(
            "studio.app.common.core.cloud.cloud_utils.get_user_storage_usage"
        ) as mock_get_storage:
            with patch(
                "studio.app.common.core.cloud.cloud_utils._is_storage_data_fresh"
            ) as mock_fresh:
                mock_db = Mock()
                mock_scope.return_value.__enter__.return_value = mock_db

                # Mock storage info: within limits
                mock_get_storage.return_value = {
                    "storage_usage_bytes": 50_000_000_000,
                    "storage_quota_bytes": 200_000_000_000,
                }
                mock_fresh.return_value = True

                # Mock subscription expired 3 days ago (in grace period)
                mock_subscription = Mock()
                mock_subscription.expiration = datetime.now(timezone.utc) - timedelta(
                    days=3
                )
                mock_db.execute.return_value.all.return_value = [[mock_subscription]]

                result = await calculate_limit_warning(user_id)

                # No warning during grace period if storage is OK
                assert result is None


@pytest.mark.asyncio
async def test_calculate_limit_warning_with_stale_cache():
    """
    Test that stale cache triggers live calculation in calculate_limit_warning
    """
    user_id = 1
    live_usage = 4_500_000_000  # 4.5 GB

    with patch("studio.app.common.core.cloud.cloud_utils.session_scope") as mock_scope:
        with patch(
            "studio.app.common.core.cloud.cloud_utils.get_user_storage_usage"
        ) as mock_get_storage:
            with patch(
                "studio.app.common.core.cloud.cloud_utils._is_storage_data_fresh"
            ) as mock_fresh:
                with patch(
                    "studio.app.common.core.cloud.cloud_utils."
                    "get_current_user_storage_usage"
                ) as mock_live:
                    mock_db = Mock()
                    mock_scope.return_value.__enter__.return_value = mock_db

                    # Mock stale cache
                    mock_get_storage.return_value = {
                        "storage_usage_bytes": 1_000_000_000,
                        "storage_quota_bytes": 5_000_000_000,
                    }
                    mock_fresh.return_value = False
                    mock_live.return_value = live_usage

                    # Mock no subscription
                    mock_db.execute.return_value.all.return_value = []

                    await calculate_limit_warning(user_id)

                    # Should use live calculation when cache is stale
                    mock_live.assert_called_once_with(user_id, force_live=True)


@pytest.mark.asyncio
async def test_calculate_limit_warning_exception_handling():
    """
    Test that exceptions are handled gracefully and return None
    """
    user_id = 1

    with patch("studio.app.common.core.cloud.cloud_utils.session_scope") as mock_scope:
        mock_scope.side_effect = Exception("Database connection failed")

        result = await calculate_limit_warning(user_id)

        assert result is None  # Should return None on exception
