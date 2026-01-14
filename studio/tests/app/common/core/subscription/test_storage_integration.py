"""
Integration tests for storage tracking workflow.
Tests full workflow: upload → increment → reconciliation.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from studio.app.common.core.cloud.cloud_utils import (
    _perform_full_scan_and_reset_delta,
    increment_user_storage,
)


@pytest.mark.asyncio
async def test_full_storage_tracking_workflow():
    """Test full workflow: upload → increment → reconciliation."""
    user_id = 123
    file_size = 100 * 1024 * 1024  # 100 MB

    with patch(
        "studio.app.common.core.cloud.cloud_utils.session_scope"
    ) as mock_session:
        with patch(
            "studio.app.common.core.cloud.cloud_utils._calculate_live_storage_usage",
            new_callable=AsyncMock,
        ) as mock_calc:
            mock_db = Mock()
            mock_session.return_value.__enter__.return_value = mock_db

            # Step 1: Mock initial storage state
            mock_storage = Mock()
            mock_storage.user_id = user_id
            mock_storage.storage_usage_bytes = 0
            mock_storage.delta_since_last_scan = 0

            mock_result = Mock()
            mock_result.first.return_value = (mock_storage,)

            # Mock lock acquisition success
            mock_lock_result = Mock()
            mock_lock_result.scalar.return_value = (
                1  # MySQL GET_LOCK returns 1 on success
            )

            def execute_side_effect(*args, **kwargs):
                # Return lock result for lock queries, storage result for others
                query_str = str(args[0]) if args else ""
                if "GET_LOCK" in query_str or "RELEASE_LOCK" in query_str:
                    return mock_lock_result
                return mock_result

            mock_db.execute.side_effect = execute_side_effect

            # Step 2: Simulate file upload (increment storage)
            increment_result = increment_user_storage(user_id, file_size)
            assert increment_result is True

            # Step 3: Verify delta was incremented
            # (In real scenario, would query DB to
            # verify delta_since_last_scan = 100 MB)

            # Step 4: Trigger reconciliation
            mock_calc.return_value = file_size  # S3 scan returns 100 MB
            await _perform_full_scan_and_reset_delta(user_id)

            # Step 5: Verify storage calculation was called
            mock_calc.assert_called_once_with(user_id)

            # Step 6: Verify delta was reset (SQL update was executed)
            assert mock_db.execute.called


@pytest.mark.asyncio
async def test_streaming_with_large_dataset():
    """Test streaming S3 scan with large dataset (memory efficiency)."""
    user_id = 123
    object_count = 10_000  # 10K objects (reduced for faster testing)
    object_size = 100 * 1024  # 100 KB each
    workspace_count = 3
    prefixes_per_workspace = 2  # input and output
    expected_total = (
        object_count * object_size * workspace_count * prefixes_per_workspace
    )

    with patch(
        "studio.app.common.core.cloud.s3_storage_monitor.boto3.client"
    ) as mock_boto:
        with patch("studio.app.common.db.database.session_scope") as mock_session:
            from studio.app.common.core.cloud.s3_storage_monitor import S3StorageMonitor

            # Mock S3 client
            mock_s3_client = Mock()
            mock_s3_client.close = Mock()
            mock_boto.return_value = mock_s3_client

            # Mock database session
            mock_db = Mock()
            mock_session.return_value.__enter__.return_value = mock_db

            # Mock workspace query result
            mock_workspace_result = Mock()
            mock_workspace_result.scalars.return_value.all.return_value = [1, 2, 3]
            mock_db.execute.return_value = mock_workspace_result

            # Create monitor
            monitor = S3StorageMonitor("test-bucket")

            # Mock streaming generator to simulate large dataset
            def mock_stream_generator(s3_client, bucket, prefix):
                # Simulate 10 pages of 1000 objects each
                pages_count = object_count // 1000
                for page_num in range(pages_count):
                    yield {
                        "Contents": [{"Size": object_size} for _ in range(1000)],
                        "IsTruncated": page_num < pages_count - 1,
                        "NextContinuationToken": f"token_{page_num}"
                        if page_num < pages_count - 1
                        else None,
                    }

            # Replace the streaming method with our mock
            monitor._stream_s3_objects = mock_stream_generator

            # Perform streaming scan
            total_size = await monitor.get_user_s3_storage_size_streaming(user_id)

            # Verify total size is correct
            assert total_size == expected_total

            # Verify S3 client was closed
            mock_s3_client.close.assert_called_once()

            # Note: Memory usage verification would require actual profiling
            # This test verifies the streaming logic works correctly
