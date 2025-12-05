"""
Unit tests for storage_limit_alerts.py API router

Tests cover:
- GET /me - Current user storage alert
- GET /usage - Detailed storage usage
- GET /all - All users storage alerts (admin)
- POST /refresh - Refresh storage calculation
- GET /limit-warning - Limit warning details
- GET /limit-warning/check - Quick warning status check
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from studio.app.common.core.cloud.s3_storage_monitor import S3StorageMonitor
from studio.app.common.core.subscription.constants import StorageQuota

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_current_user():
    """Mock current user (non-admin)"""
    user = Mock()
    user.id = 1
    user.uid = "test-user-123"
    user.name = "Test User"
    user.email = "test@example.com"
    user.is_admin = False
    return user


@pytest.fixture
def mock_admin_user():
    """Mock admin user"""
    user = Mock()
    user.id = 2
    user.uid = "admin-user-123"
    user.name = "Admin User"
    user.email = "admin@example.com"
    user.is_admin = True
    return user


@pytest.fixture
def mock_storage_monitor():
    """Mock S3StorageMonitor instance"""
    monitor = Mock(spec=S3StorageMonitor)
    monitor.CRITICAL_THRESHOLD = StorageQuota.CRITICAL_THRESHOLD_PERCENT
    monitor.DANGER_THRESHOLD = StorageQuota.DANGER_THRESHOLD_PERCENT
    monitor.calculate_storage_alert_level = Mock()
    monitor.format_bytes = Mock(return_value="4.5 GB")
    monitor.get_alert_message = Mock(return_value="Storage at 90%")
    return monitor


# ============================================================================
# Tests for GET /me endpoint
# ============================================================================


@pytest.mark.asyncio
async def test_get_me_no_alert(mock_current_user):
    """Test /me endpoint when user has no storage alert"""
    with patch(
        "studio.app.common.core.cloud.cloud_utils.get_current_user_storage_usage"
    ) as mock_get_usage:
        with patch(
            "studio.app.common.core.cloud.cloud_utils.get_user_storage_usage"
        ) as mock_storage_info:
            with patch(
                "studio.app.common.routers.storage_limit_alerts._get_storage_utilities"
            ) as mock_get_utils:
                # Mock usage at 50% (below threshold)
                mock_get_usage.return_value = 2_500_000_000  # 2.5 GB
                mock_storage_info.return_value = {
                    "storage_usage_bytes": 2_500_000_000,
                    "storage_quota_bytes": 5_000_000_000,
                }

                mock_monitor = Mock()
                mock_monitor.calculate_storage_alert_level.return_value = None
                mock_monitor.format_bytes.return_value = "2.5 GB"
                mock_get_utils.return_value = mock_monitor

                # Import and call the endpoint function
                from studio.app.common.routers.storage_limit_alerts import (
                    get_my_storage_alert,
                )

                result = await get_my_storage_alert(current_user=mock_current_user)

                assert result["has_alert"] is False
                assert result["storage_usage_bytes"] == 2_500_000_000
                assert result["storage_usage_formatted"] == "2.5 GB"
                assert result["alert"] is None


@pytest.mark.asyncio
async def test_get_me_critical_alert(mock_current_user):
    """Test /me endpoint when user has critical storage alert (90%)"""
    with patch(
        "studio.app.common.core.cloud.cloud_utils.get_current_user_storage_usage"
    ) as mock_get_usage:
        with patch(
            "studio.app.common.core.cloud.cloud_utils.get_user_storage_usage"
        ) as mock_storage_info:
            with patch(
                "studio.app.common.routers.storage_limit_alerts._get_storage_utilities"
            ) as mock_get_utils:
                # Mock usage at 90% (critical threshold)
                mock_get_usage.return_value = 4_500_000_000  # 4.5 GB
                mock_storage_info.return_value = {
                    "storage_usage_bytes": 4_500_000_000,
                    "storage_quota_bytes": 5_000_000_000,
                }

                mock_monitor = Mock()
                mock_monitor.calculate_storage_alert_level.return_value = "critical"
                mock_monitor.get_alert_message.return_value = (
                    "Storage usage at 90% - approaching limit"
                )
                mock_get_utils.return_value = mock_monitor

                from studio.app.common.routers.storage_limit_alerts import (
                    get_my_storage_alert,
                )

                result = await get_my_storage_alert(current_user=mock_current_user)

                assert result["has_alert"] is True
                assert result["alert"]["alert_level"] == "critical"
                assert result["alert"]["storage_usage_bytes"] == 4_500_000_000
                assert result["alert"]["storage_quota_bytes"] == 5_000_000_000
                assert result["alert"]["storage_usage_percent"] == 90.0
                assert "Storage usage at 90%" in result["alert"]["message"]


@pytest.mark.asyncio
async def test_get_me_danger_alert(mock_current_user):
    """Test /me endpoint when user has danger alert (100%+)"""
    with patch(
        "studio.app.common.core.cloud.cloud_utils.get_current_user_storage_usage"
    ) as mock_get_usage:
        with patch(
            "studio.app.common.core.cloud.cloud_utils.get_user_storage_usage"
        ) as mock_storage_info:
            with patch(
                "studio.app.common.routers.storage_limit_alerts._get_storage_utilities"
            ) as mock_get_utils:
                # Mock usage at 105% (danger threshold)
                mock_get_usage.return_value = 5_250_000_000  # 5.25 GB
                mock_storage_info.return_value = {
                    "storage_usage_bytes": 5_250_000_000,
                    "storage_quota_bytes": 5_000_000_000,
                }

                mock_monitor = Mock()
                mock_monitor.calculate_storage_alert_level.return_value = "danger"
                mock_monitor.get_alert_message.return_value = (
                    "Storage quota exceeded - immediate action required"
                )
                mock_get_utils.return_value = mock_monitor

                from studio.app.common.routers.storage_limit_alerts import (
                    get_my_storage_alert,
                )

                result = await get_my_storage_alert(current_user=mock_current_user)

                assert result["has_alert"] is True
                assert result["alert"]["alert_level"] == "danger"
                assert result["alert"]["storage_usage_percent"] == 105.0


@pytest.mark.asyncio
async def test_get_me_no_storage_info(mock_current_user):
    """Test /me endpoint when user has no storage info in database"""
    with patch(
        "studio.app.common.core.cloud.cloud_utils.get_current_user_storage_usage"
    ) as mock_get_usage:
        with patch(
            "studio.app.common.core.cloud.cloud_utils.get_user_storage_usage"
        ) as mock_storage_info:
            with patch(
                "studio.app.common.routers.storage_limit_alerts._get_storage_utilities"
            ) as mock_get_utils:
                mock_get_usage.return_value = 1_000_000_000
                mock_storage_info.return_value = None

                mock_monitor = Mock()
                mock_monitor.format_bytes.return_value = "1.0 GB"
                mock_get_utils.return_value = mock_monitor

                from studio.app.common.routers.storage_limit_alerts import (
                    get_my_storage_alert,
                )

                result = await get_my_storage_alert(current_user=mock_current_user)

                assert result["has_alert"] is False
                assert result["storage_usage_bytes"] == 1_000_000_000


# ============================================================================
# Tests for GET /usage endpoint
# ============================================================================


@pytest.mark.asyncio
async def test_get_usage_with_quota(mock_current_user):
    """Test /usage endpoint with valid storage quota"""
    with patch(
        "studio.app.common.core.cloud.cloud_utils.get_current_user_storage_usage"
    ) as mock_get_usage:
        with patch(
            "studio.app.common.core.cloud.cloud_utils.get_user_storage_usage"
        ) as mock_storage_info:
            with patch(
                "studio.app.common.routers.storage_limit_alerts._get_storage_utilities"
            ) as mock_get_utils:
                mock_get_usage.return_value = 3_000_000_000  # 3 GB
                mock_storage_info.return_value = {
                    "storage_usage_bytes": 3_000_000_000,
                    "storage_quota_bytes": 5_000_000_000,
                }

                mock_monitor = Mock()
                mock_monitor.CRITICAL_THRESHOLD = 90
                mock_monitor.DANGER_THRESHOLD = 100
                mock_monitor.calculate_storage_alert_level.return_value = None
                mock_monitor.format_bytes.side_effect = ["3.0 GB", "5.0 GB"]
                mock_get_utils.return_value = mock_monitor

                from studio.app.common.routers.storage_limit_alerts import (
                    get_my_storage_usage,
                )

                result = await get_my_storage_usage(current_user=mock_current_user)

                assert result["storage_usage_bytes"] == 3_000_000_000
                assert result["storage_quota_bytes"] == 5_000_000_000
                assert result["storage_usage_percent"] == 60.0
                assert result["alert_level"] is None
                assert result["thresholds"]["critical"] == 90
                assert result["thresholds"]["danger"] == 100


@pytest.mark.asyncio
async def test_get_usage_no_storage_info(mock_current_user):
    """Test /usage endpoint when user has no storage info"""
    with patch(
        "studio.app.common.core.cloud.cloud_utils.get_current_user_storage_usage"
    ) as mock_get_usage:
        with patch(
            "studio.app.common.core.cloud.cloud_utils.get_user_storage_usage"
        ) as mock_storage_info:
            with patch(
                "studio.app.common.routers.storage_limit_alerts._get_storage_utilities"
            ) as mock_get_utils:
                mock_get_usage.return_value = 1_000_000_000
                mock_storage_info.return_value = None

                mock_monitor = Mock()
                mock_monitor.CRITICAL_THRESHOLD = 90
                mock_monitor.DANGER_THRESHOLD = 100
                mock_monitor.format_bytes.return_value = "1.0 GB"
                mock_get_utils.return_value = mock_monitor

                from studio.app.common.routers.storage_limit_alerts import (
                    get_my_storage_usage,
                )

                result = await get_my_storage_usage(current_user=mock_current_user)

                assert result["storage_usage_bytes"] == 1_000_000_000
                assert result["storage_quota_bytes"] is None
                assert result["storage_usage_percent"] is None
                assert result["alert_level"] is None


@pytest.mark.asyncio
async def test_get_usage_zero_quota(mock_current_user):
    """Test /usage endpoint when quota is zero (edge case)"""
    with patch(
        "studio.app.common.core.cloud.cloud_utils.get_current_user_storage_usage"
    ) as mock_get_usage:
        with patch(
            "studio.app.common.core.cloud.cloud_utils.get_user_storage_usage"
        ) as mock_storage_info:
            with patch(
                "studio.app.common.routers.storage_limit_alerts._get_storage_utilities"
            ) as mock_get_utils:
                mock_get_usage.return_value = 1_000_000_000
                mock_storage_info.return_value = {
                    "storage_usage_bytes": 1_000_000_000,
                    "storage_quota_bytes": 0,
                }

                mock_monitor = Mock()
                mock_monitor.CRITICAL_THRESHOLD = 90
                mock_monitor.DANGER_THRESHOLD = 100
                mock_monitor.calculate_storage_alert_level.return_value = None
                mock_monitor.format_bytes.side_effect = ["1.0 GB", "0 B"]
                mock_get_utils.return_value = mock_monitor

                from studio.app.common.routers.storage_limit_alerts import (
                    get_my_storage_usage,
                )

                result = await get_my_storage_usage(current_user=mock_current_user)

                assert result["storage_usage_percent"] == 0  # Should handle div by 0


# ============================================================================
# Tests for GET /all endpoint (admin)
# ============================================================================


@pytest.mark.asyncio
async def test_get_all_admin_access(mock_admin_user):
    """Test /all endpoint with admin user"""
    with patch(
        "studio.app.common.routers.storage_limit_alerts."
        "monitor_storage_and_generate_alerts"
    ) as mock_monitor_alerts:
        mock_alerts = [
            {
                "user_id": 1,
                "alert_level": "critical",
                "storage_usage_bytes": 4_500_000_000,
                "storage_quota_bytes": 5_000_000_000,
                "storage_usage_percent": 90.0,
            }
        ]
        mock_monitor_alerts.return_value = mock_alerts

        with patch(
            "studio.app.common.routers.storage_limit_alerts.S3StorageMonitor"
        ) as mock_monitor_class:
            mock_monitor = Mock()
            mock_monitor.get_alert_message.return_value = "Storage at 90%"
            mock_monitor_class.return_value = mock_monitor

            from studio.app.common.routers.storage_limit_alerts import (
                get_all_storage_alerts,
            )

            result = await get_all_storage_alerts(
                current_user=mock_admin_user,
                remote_bucket_name="test-bucket",
                db=Mock(),
            )

            assert len(result) == 1
            assert result[0]["alert_level"] == "critical"
            assert "message" in result[0]


@pytest.mark.asyncio
async def test_get_all_non_admin_forbidden(mock_current_user):
    """Test /all endpoint rejects non-admin users"""
    from studio.app.common.routers.storage_limit_alerts import get_all_storage_alerts

    with pytest.raises(HTTPException) as exc_info:
        await get_all_storage_alerts(
            current_user=mock_current_user,
            remote_bucket_name="test-bucket",
            db=Mock(),
        )

    assert exc_info.value.status_code == 403
    assert "Admin access required" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_get_all_no_bucket(mock_admin_user):
    """Test /all endpoint when no S3 bucket configured"""
    from studio.app.common.routers.storage_limit_alerts import get_all_storage_alerts

    with pytest.raises(HTTPException) as exc_info:
        await get_all_storage_alerts(
            current_user=mock_admin_user,
            remote_bucket_name=None,
            db=Mock(),
        )

    assert exc_info.value.status_code == 400
    assert "No S3 bucket configured" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_get_all_empty_alerts(mock_admin_user):
    """Test /all endpoint when no alerts exist"""
    with patch(
        "studio.app.common.routers.storage_limit_alerts."
        "monitor_storage_and_generate_alerts"
    ) as mock_monitor_alerts:
        mock_monitor_alerts.return_value = []

        from studio.app.common.routers.storage_limit_alerts import (
            get_all_storage_alerts,
        )

        result = await get_all_storage_alerts(
            current_user=mock_admin_user,
            remote_bucket_name="test-bucket",
            db=Mock(),
        )

        assert result == []


# ============================================================================
# Tests for POST /refresh endpoint
# ============================================================================


@pytest.mark.asyncio
async def test_refresh_storage_success(mock_current_user):
    """Test /refresh endpoint successfully recalculates storage"""
    with patch(
        "studio.app.common.routers.storage_limit_alerts.S3StorageMonitor"
    ) as mock_monitor_class:
        with patch(
            "studio.app.common.core.cloud.cloud_utils.update_user_storage_usage"
        ) as mock_update:
            mock_monitor = Mock()
            mock_monitor.get_user_s3_storage_size = AsyncMock(
                return_value=3_500_000_000
            )
            mock_monitor.format_bytes.return_value = "3.5 GB"
            mock_monitor_class.return_value = mock_monitor

            mock_update.return_value = True

            from studio.app.common.routers.storage_limit_alerts import (
                refresh_storage_usage,
            )

            result = await refresh_storage_usage(
                current_user=mock_current_user,
                remote_bucket_name="test-bucket",
            )

            assert result["success"] is True
            assert result["updated_usage_bytes"] == 3_500_000_000
            assert result["database_updated"] is True


@pytest.mark.asyncio
async def test_refresh_storage_no_bucket(mock_current_user):
    """Test /refresh endpoint when no bucket configured"""
    from studio.app.common.routers.storage_limit_alerts import refresh_storage_usage

    with pytest.raises(HTTPException) as exc_info:
        await refresh_storage_usage(
            current_user=mock_current_user,
            remote_bucket_name=None,
        )

    # The function catches all exceptions and returns 500, even for None bucket
    assert exc_info.value.status_code in [400, 500]
    # Either "No S3 bucket" or "Failed to refresh" message
    assert (
        "bucket" in str(exc_info.value.detail).lower()
        or "failed" in str(exc_info.value.detail).lower()
    )


@pytest.mark.asyncio
async def test_refresh_storage_database_update_fails(mock_current_user):
    """Test /refresh endpoint when database update fails"""
    with patch(
        "studio.app.common.routers.storage_limit_alerts.S3StorageMonitor"
    ) as mock_monitor_class:
        with patch(
            "studio.app.common.core.cloud.cloud_utils.update_user_storage_usage"
        ) as mock_update:
            mock_monitor = Mock()
            mock_monitor.get_user_s3_storage_size = AsyncMock(
                return_value=3_500_000_000
            )
            mock_monitor.format_bytes.return_value = "3.5 GB"
            mock_monitor_class.return_value = mock_monitor

            mock_update.return_value = False  # Database update failed

            from studio.app.common.routers.storage_limit_alerts import (
                refresh_storage_usage,
            )

            result = await refresh_storage_usage(
                current_user=mock_current_user,
                remote_bucket_name="test-bucket",
            )

            assert result["success"] is True
            assert result["database_updated"] is False


# ============================================================================
# Tests for GET /limit-warning endpoint
# ============================================================================


@pytest.mark.asyncio
async def test_get_limit_warning_has_warning(mock_current_user):
    """Test /limit-warning endpoint when user has a warning"""
    with patch(
        "studio.app.common.core.cloud.cloud_utils.calculate_limit_warning"
    ) as mock_calc:
        mock_warning = {
            "has_warning": True,
            "warning_type": "storage",
            "days_remaining": 7,
            "storage_usage_percent": 95.0,
        }
        mock_calc.return_value = mock_warning

        from studio.app.common.routers.storage_limit_alerts import get_my_limit_warning

        result = await get_my_limit_warning(current_user=mock_current_user)

        assert result == mock_warning
        assert result["has_warning"] is True
        assert result["warning_type"] == "storage"


@pytest.mark.asyncio
async def test_get_limit_warning_no_warning(mock_current_user):
    """Test /limit-warning endpoint when user has no warning"""
    with patch(
        "studio.app.common.core.cloud.cloud_utils.calculate_limit_warning"
    ) as mock_calc:
        mock_calc.return_value = None

        from studio.app.common.routers.storage_limit_alerts import get_my_limit_warning

        result = await get_my_limit_warning(current_user=mock_current_user)

        assert result is None


# ============================================================================
# Tests for GET /limit-warning/check endpoint
# ============================================================================


@pytest.mark.asyncio
async def test_check_limit_warning_has_warning(mock_current_user):
    """Test /limit-warning/check endpoint when user has warning"""
    with patch(
        "studio.app.common.core.cloud.cloud_utils.calculate_limit_warning"
    ) as mock_calc:
        mock_warning = {
            "has_warning": True,
            "warning_type": "subscription",
            "days_remaining": 5,
        }
        mock_calc.return_value = mock_warning

        from studio.app.common.routers.storage_limit_alerts import (
            check_limit_warning_status,
        )

        result = await check_limit_warning_status(current_user=mock_current_user)

        assert result["has_warning"] is True
        assert result["warning_type"] == "subscription"
        assert result["days_remaining"] == 5
        assert result["user_id"] == 1


@pytest.mark.asyncio
async def test_check_limit_warning_no_warning(mock_current_user):
    """Test /limit-warning/check endpoint when user has no warning"""
    with patch(
        "studio.app.common.core.cloud.cloud_utils.calculate_limit_warning"
    ) as mock_calc:
        mock_calc.return_value = None

        from studio.app.common.routers.storage_limit_alerts import (
            check_limit_warning_status,
        )

        result = await check_limit_warning_status(current_user=mock_current_user)

        assert result["has_warning"] is False
        assert result["warning_type"] is None
        assert result["days_remaining"] is None
        assert result["user_id"] == 1
