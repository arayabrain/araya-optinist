"""
Integration tests for dataview publish endpoint with optimistic locking.

Tests concurrent publish/unpublish operations.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from studio.app.common.routers.dataview import publish_dataview_records
from studio.app.common.schemas.dataview import LocalSyncStatus


class TestPublishDataviewRecords:
    """Test publish endpoint with optimistic locking"""

    @pytest.mark.asyncio
    async def test_publish_success(self):
        """Test successful publish operation"""
        mock_db = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 123
        mock_user.remote_bucket_name = "test-bucket"
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.workspace_id = "1"
        mock_record.uid = "test_uid"
        mock_record.publish_status = 0
        mock_record.local_sync_status = LocalSyncStatus.synced.value
        mock_record.version = 0

        # Setup mock result for execute() with rowcount=1 (success)
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_db.execute.return_value = mock_result

        # Mock validation result
        mock_validation = MagicMock()
        mock_validation.can_publish = True

        with patch(
            "studio.app.common.routers.dataview.DataviewService."
            "find_user_owned_dataview_record",
            return_value=mock_record,
        ), patch(
            "studio.app.common.routers.dataview._validate_experiment_exists_in_s3",
            return_value=(True, None),
        ), patch(
            "studio.app.common.routers.dataview.PublishValidator.validate",
            return_value=mock_validation,
        ):
            from studio.app.common.routers.dataview import PublishFlags

            result = await publish_dataview_records(
                id=1, flag=PublishFlags.on, db=mock_db, current_user=mock_user
            )

        assert result is True
        # Verify execute() was called (the actual SQL update)
        assert mock_db.execute.called
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_unpublish_success(self):
        mock_db = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 123
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.local_sync_status = LocalSyncStatus.synced.value
        mock_record.version = 0
        """Test successful unpublish operation"""
        mock_record.publish_status = 1

        # Setup mock result for execute() with rowcount=1 (success)
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_db.execute.return_value = mock_result

        with patch(
            "studio.app.common.routers.dataview.DataviewService."
            "find_user_owned_dataview_record",
            return_value=mock_record,
        ):
            from studio.app.common.routers.dataview import PublishFlags

            result = await publish_dataview_records(
                id=1, flag=PublishFlags.off, db=mock_db, current_user=mock_user
            )

        assert result is True
        # Verify execute() was called (the actual SQL update)
        assert mock_db.execute.called
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_publish_no_change_needed(self):
        """Test publish when already published"""
        mock_db = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 123
        mock_user.remote_bucket_name = "test-bucket"
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.workspace_id = "1"
        mock_record.uid = "test_uid"
        mock_record.version = 0
        mock_record.publish_status = 1

        # Mock validation result
        mock_validation = MagicMock()
        mock_validation.can_publish = True

        with patch(
            "studio.app.common.routers.dataview.DataviewService."
            "find_user_owned_dataview_record",
            return_value=mock_record,
        ), patch(
            "studio.app.common.routers.dataview.PublishValidator.validate",
            return_value=mock_validation,
        ):
            from studio.app.common.routers.dataview import PublishFlags

            result = await publish_dataview_records(
                id=1, flag=PublishFlags.on, db=mock_db, current_user=mock_user
            )

        assert result is True
        assert mock_record.version == 0  # Version not incremented
        mock_db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_publish_record_not_found(self):
        mock_db = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 123
        """Test publish when record not found"""
        with patch(
            "studio.app.common.routers.dataview.DataviewService."
            "find_user_owned_dataview_record",
            return_value=None,
        ):
            from studio.app.common.routers.dataview import PublishFlags

            with pytest.raises(HTTPException) as exc_info:
                await publish_dataview_records(
                    id=1, flag=PublishFlags.on, db=mock_db, current_user=mock_user
                )

            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_publish_concurrent_modification_retry(self):
        """Test retry on concurrent modification"""
        mock_db = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 123
        mock_user.remote_bucket_name = "test-bucket"
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.workspace_id = "1"
        mock_record.uid = "test_uid"
        mock_record.publish_status = 0
        mock_record.local_sync_status = LocalSyncStatus.synced.value
        mock_record.version = 0

        call_count = 0

        def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            if call_count == 1:
                # First attempt: version conflict (rowcount=0)
                mock_result.rowcount = 0
            else:
                # Second attempt: success (rowcount=1)
                mock_result.rowcount = 1
            return mock_result

        mock_db.execute.side_effect = mock_execute

        # Mock validation result
        mock_validation = MagicMock()
        mock_validation.can_publish = True

        with patch(
            "studio.app.common.routers.dataview.DataviewService."
            "find_user_owned_dataview_record",
            return_value=mock_record,
        ), patch(
            "studio.app.common.routers.dataview._validate_experiment_exists_in_s3",
            return_value=(True, None),
        ), patch(
            "studio.app.common.routers.dataview.PublishValidator.validate",
            return_value=mock_validation,
        ):
            from studio.app.common.routers.dataview import PublishFlags

            result = await publish_dataview_records(
                id=1, flag=PublishFlags.on, db=mock_db, current_user=mock_user
            )

        assert result is True
        assert call_count == 2  # Retried once
        # commit is called on each attempt
        assert mock_db.commit.call_count == 2

    @pytest.mark.asyncio
    async def test_publish_concurrent_modification_max_retries(self):
        """Test failure after max retries on concurrent modification"""
        mock_db = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 123
        mock_user.remote_bucket_name = "test-bucket"
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.workspace_id = "1"
        mock_record.uid = "test_uid"
        mock_record.publish_status = 0
        mock_record.local_sync_status = LocalSyncStatus.synced.value
        mock_record.version = 0

        # Always return rowcount=0 to simulate persistent version conflicts
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_db.execute.return_value = mock_result

        # Mock validation result
        mock_validation = MagicMock()
        mock_validation.can_publish = True

        with patch(
            "studio.app.common.routers.dataview.DataviewService."
            "find_user_owned_dataview_record",
            return_value=mock_record,
        ), patch(
            "studio.app.common.routers.dataview._validate_experiment_exists_in_s3",
            return_value=(True, None),
        ), patch(
            "studio.app.common.routers.dataview.PublishValidator.validate",
            return_value=mock_validation,
        ):
            from studio.app.common.routers.dataview import PublishFlags

            with pytest.raises(HTTPException) as exc_info:
                await publish_dataview_records(
                    id=1, flag=PublishFlags.on, db=mock_db, current_user=mock_user
                )

            assert exc_info.value.status_code == 409
            assert "Concurrent modification" in exc_info.value.detail


class TestPublicDataviewReproduceWorkflow:
    """Test public dataview reproduce endpoint"""

    @pytest.mark.asyncio
    async def test_reproduce_pending_sync_returns_202(self):
        """Test 202 response when experiment is pending sync and data not available"""
        from unittest.mock import AsyncMock

        from studio.app.common.routers.dataview import public_reproduce_experiment

        mock_record = MagicMock()
        mock_record.local_sync_status = LocalSyncStatus.pending.value

        mock_remote_controller = MagicMock()
        mock_remote_controller.download_experiment = AsyncMock(return_value=True)

        mock_remote_reader = AsyncMock()
        mock_remote_reader.__aenter__ = AsyncMock(return_value=mock_remote_controller)
        mock_remote_reader.__aexit__ = AsyncMock(return_value=False)

        mock_validation = MagicMock()
        mock_validation.is_displayable = False
        mock_validation.reason = "Data not available"

        with patch(
            "studio.app.common.routers.dataview.DataviewService."
            "find_published_dataview_record",
            return_value=mock_record,
        ):
            with patch(
                "studio.app.common.routers.dataview.RemoteSyncStatusFileUtil."
                "check_sync_status_unsynced",
                return_value=False,
            ):
                with patch("os.environ.get", return_value="test-bucket"):
                    with patch(
                        "studio.app.common.routers.dataview.RemoteStorageReader",
                        return_value=mock_remote_reader,
                    ):
                        with patch(
                            "studio.app.common.routers.dataview.PublishValidator."
                            "validate_for_display",
                            return_value=mock_validation,
                        ):
                            response = await public_reproduce_experiment(
                                workspace_id="1", unique_id="exp123", db=MagicMock()
                            )

        assert response.status_code == 202
        assert "pending_sync" in response.body.decode()

    @pytest.mark.asyncio
    async def test_reproduce_sync_error_returns_503(self):
        """Test 503 response when experiment has sync error and data not available"""
        from unittest.mock import AsyncMock

        from studio.app.common.routers.dataview import public_reproduce_experiment

        mock_record = MagicMock()
        mock_record.local_sync_status = LocalSyncStatus.error.value

        mock_remote_controller = MagicMock()
        mock_remote_controller.download_experiment = AsyncMock(return_value=True)

        mock_remote_reader = AsyncMock()
        mock_remote_reader.__aenter__ = AsyncMock(return_value=mock_remote_controller)
        mock_remote_reader.__aexit__ = AsyncMock(return_value=False)

        mock_validation = MagicMock()
        mock_validation.is_displayable = False
        mock_validation.reason = "Data not available"

        with patch(
            "studio.app.common.routers.dataview.DataviewService."
            "find_published_dataview_record",
            return_value=mock_record,
        ):
            with patch(
                "studio.app.common.routers.dataview.RemoteSyncStatusFileUtil."
                "check_sync_status_unsynced",
                return_value=False,
            ):
                with patch("os.environ.get", return_value="test-bucket"):
                    with patch(
                        "studio.app.common.routers.dataview.RemoteStorageReader",
                        return_value=mock_remote_reader,
                    ):
                        with patch(
                            "studio.app.common.routers.dataview.PublishValidator."
                            "validate_for_display",
                            return_value=mock_validation,
                        ):
                            response = await public_reproduce_experiment(
                                workspace_id="1", unique_id="exp123", db=MagicMock()
                            )

        assert response.status_code == 503
        assert "data_error" in response.body.decode()

    @pytest.mark.asyncio
    async def test_reproduce_downloads_from_s3_if_missing(self):
        """Test S3 download when experiment not on local EBS"""
        from unittest.mock import AsyncMock

        from studio.app.common.routers.dataview import public_reproduce_experiment

        mock_record = MagicMock()
        mock_record.local_sync_status = LocalSyncStatus.synced.value

        mock_remote_controller = MagicMock()
        # Use AsyncMock for async method
        mock_remote_controller.download_experiment = AsyncMock(return_value=True)

        mock_remote_reader = AsyncMock()
        mock_remote_reader.__aenter__ = AsyncMock(return_value=mock_remote_controller)
        mock_remote_reader.__aexit__ = AsyncMock(return_value=False)

        mock_validation = MagicMock()
        mock_validation.is_displayable = True

        with patch(
            "studio.app.common.routers.dataview.DataviewService."
            "find_published_dataview_record",
            return_value=mock_record,
        ):
            with patch(
                "studio.app.common.routers.dataview.RemoteSyncStatusFileUtil."
                "check_sync_status_unsynced",
                return_value=True,
            ):
                with patch("os.environ.get", return_value="test-bucket"):
                    with patch(
                        "studio.app.common.routers.dataview.RemoteStorageReader",
                        return_value=mock_remote_reader,
                    ):
                        with patch(
                            "studio.app.common.routers.dataview.PublishValidator."
                            "validate_for_display",
                            return_value=mock_validation,
                        ):
                            with patch(
                                "studio.app.common.routers.dataview."
                                "reproduce_experiment"
                            ):
                                await public_reproduce_experiment(
                                    workspace_id="1", unique_id="exp123", db=MagicMock()
                                )

        mock_remote_controller.download_experiment.assert_called_once()

    @pytest.mark.asyncio
    async def test_reproduce_auto_updates_sync_status_when_data_available(self):
        """Test sync status auto-updates to synced when data is available"""
        from unittest.mock import AsyncMock

        from studio.app.common.routers.dataview import public_reproduce_experiment

        mock_record = MagicMock()
        mock_record.local_sync_status = LocalSyncStatus.error.value

        mock_remote_controller = MagicMock()
        mock_remote_controller.download_experiment = AsyncMock(return_value=True)

        mock_remote_reader = AsyncMock()
        mock_remote_reader.__aenter__ = AsyncMock(return_value=mock_remote_controller)
        mock_remote_reader.__aexit__ = AsyncMock(return_value=False)

        mock_validation = MagicMock()
        mock_validation.is_displayable = True

        mock_db = MagicMock()

        with patch(
            "studio.app.common.routers.dataview.DataviewService."
            "find_published_dataview_record",
            return_value=mock_record,
        ):
            with patch(
                "studio.app.common.routers.dataview.RemoteSyncStatusFileUtil."
                "check_sync_status_unsynced",
                return_value=False,
            ):
                with patch("os.environ.get", return_value="test-bucket"):
                    with patch(
                        "studio.app.common.routers.dataview.RemoteStorageReader",
                        return_value=mock_remote_reader,
                    ):
                        with patch(
                            "studio.app.common.routers.dataview.PublishValidator."
                            "validate_for_display",
                            return_value=mock_validation,
                        ):
                            with patch(
                                "studio.app.common.routers.dataview."
                                "reproduce_experiment"
                            ):
                                await public_reproduce_experiment(
                                    workspace_id="1", unique_id="exp123", db=mock_db
                                )

        # Verify sync status was updated
        assert mock_record.local_sync_status == LocalSyncStatus.synced.value
        mock_db.add.assert_called_once_with(mock_record)
        mock_db.commit.assert_called_once()
