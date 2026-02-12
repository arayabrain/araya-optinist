"""
Unit tests for storage tracking functionality.
Tests incremental tracking, threshold triggers, and advisory locks.
"""

from unittest.mock import Mock, patch

import pytest

from studio.app.common.core.cloud.cloud_utils import (
    _perform_full_scan_and_reset_delta,
    _should_trigger_full_scan,
    increment_user_storage,
)
from studio.app.common.core.utils.datetime_utils import get_current_datetime


@pytest.mark.asyncio
async def test_increment_user_storage():
    """Test incremental storage tracking - increment operation."""
    user_id = 123
    initial_storage = 1000000000  # 1 GB
    bytes_to_add = 100 * 1024 * 1024  # 100 MB

    with patch(
        "studio.app.common.core.cloud.cloud_utils.session_scope"
    ) as mock_session:
        mock_db = Mock()
        mock_session.return_value.__enter__.return_value = mock_db

        # Mock existing storage record
        mock_storage = Mock()
        mock_storage.user_id = user_id
        mock_storage.storage_usage_bytes = initial_storage
        mock_storage.delta_since_last_scan = 0

        mock_result = Mock()
        mock_result.first.return_value = (mock_storage,)
        mock_db.execute.return_value = mock_result

        # Perform increment
        result = increment_user_storage(user_id, bytes_to_add)

        assert result is True
        # Verify execute was called (SQL update statement)
        assert mock_db.execute.called


@pytest.mark.asyncio
async def test_should_trigger_full_scan():
    """Test threshold triggers for full S3 scans."""
    user_id = 123
    current_storage = 1000000000  # 1 GB

    # Test case 1: Delta above 5% threshold
    with patch(
        "studio.app.common.core.cloud.cloud_utils.session_scope"
    ) as mock_session:
        mock_db = Mock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_storage = Mock()
        mock_storage.user_id = user_id
        mock_storage.storage_usage_bytes = current_storage
        mock_storage.delta_since_last_scan = int(0.06 * current_storage)  # 6% delta
        mock_storage.last_full_scan = None

        mock_result = Mock()
        mock_result.first.return_value = (mock_storage,)
        mock_db.execute.return_value = mock_result

        should_scan = await _should_trigger_full_scan(user_id)
        assert should_scan is True

    # Test case 2: Delta below 5% threshold
    with patch(
        "studio.app.common.core.cloud.cloud_utils.session_scope"
    ) as mock_session:
        mock_db = Mock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_storage = Mock()
        mock_storage.user_id = user_id
        mock_storage.storage_usage_bytes = current_storage
        mock_storage.delta_since_last_scan = int(0.03 * current_storage)  # 3% delta
        mock_storage.last_full_scan = get_current_datetime()

        mock_result = Mock()
        mock_result.first.return_value = (mock_storage,)
        mock_db.execute.return_value = mock_result

        should_scan = await _should_trigger_full_scan(user_id)
        assert should_scan is False


@pytest.mark.asyncio
async def test_advisory_lock_prevents_concurrent_scans():
    """Test that advisory locks prevent concurrent scans of the same user."""
    user_id = 123

    with patch(
        "studio.app.common.core.cloud.cloud_utils.session_scope"
    ) as mock_session:
        with patch(
            "studio.app.common.core.cloud.cloud_utils._calculate_live_storage_usage"
        ) as mock_calc:
            mock_db = Mock()
            mock_session.return_value.__enter__.return_value = mock_db

            # Simulate lock acquisition failure (another process has the lock)
            mock_lock_result = Mock()
            mock_lock_result.scalar.return_value = False  # Lock NOT acquired
            mock_db.execute.return_value = mock_lock_result

            # Attempt to perform scan
            await _perform_full_scan_and_reset_delta(user_id)

            # Verify that storage calculation was NOT called (scan was skipped)
            mock_calc.assert_not_called()
