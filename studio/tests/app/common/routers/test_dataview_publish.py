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
            "find_dataview_record",
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
            "find_dataview_record",
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
            "find_dataview_record",
            return_value=mock_record,
        ):
            with patch(
                "studio.app.common.routers.dataview.RemoteSyncStatusFileUtil."
                "check_sync_status_unsynced",
                return_value=True,
            ):
                with patch(
                    "studio.app.common.routers.dataview."
                    "RemoteStorageController.is_available",
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
                                        workspace_id="1",
                                        unique_id="exp123",
                                        db=MagicMock(),
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
        mock_db.execute.return_value.rowcount = 1

        with patch(
            "studio.app.common.routers.dataview.DataviewService."
            "find_dataview_record",
            return_value=mock_record,
        ), patch("os.path.exists", return_value=True), patch(
            "os.environ.get", return_value="test-bucket"
        ), patch(
            "studio.app.common.routers.dataview.PublishValidator."
            "validate_for_display",
            return_value=mock_validation,
        ), patch(
            "studio.app.common.routers.dataview.reproduce_experiment"
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

        # Verify bulk update was executed and committed
        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_reproduce_synced_but_missing_in_s3_demotes_to_error(self):
        """A synced row whose files are gone from S3 is demoted to error."""
        from unittest.mock import AsyncMock

        from studio.app.common.routers.dataview import public_reproduce_experiment

        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.local_sync_status = LocalSyncStatus.synced.value

        mock_validation = MagicMock()
        mock_validation.is_displayable = False
        mock_validation.reason = "Data not available"

        mock_db = MagicMock()

        with patch(
            "studio.app.common.routers.dataview.DataviewService."
            "find_dataview_record",
            return_value=mock_record,
        ), patch(
            "studio.app.common.routers.dataview.RemoteSyncStatusFileUtil."
            "check_sync_status_unsynced",
            return_value=False,
        ), patch(
            "studio.app.common.routers.dataview.RemoteStorageController."
            "is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.dataview."
            "_resolve_workspace_remote_bucket_name",
            return_value="test-bucket",
        ), patch(
            "studio.app.common.routers.dataview._validate_experiment_exists_in_s3",
            new=AsyncMock(return_value=(False, "No data found in S3")),
        ), patch(
            "studio.app.common.routers.dataview.PublishValidator."
            "validate_for_display",
            return_value=mock_validation,
        ):
            response = await public_reproduce_experiment(
                workspace_id="1", unique_id="exp123", db=mock_db
            )

        assert response.status_code == 503
        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()
        # Pin that the statement demotes synced -> error (SET target and guard)
        params = mock_db.execute.call_args[0][0].compile().params
        assert params["local_sync_status"] == LocalSyncStatus.error.value
        assert LocalSyncStatus.synced.value in params.values()

    @pytest.mark.asyncio
    async def test_reproduce_synced_present_in_s3_does_not_demote(self):
        """A synced row still present in S3 is not demoted (transient local miss)."""
        from unittest.mock import AsyncMock

        from studio.app.common.routers.dataview import public_reproduce_experiment

        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.local_sync_status = LocalSyncStatus.synced.value

        mock_validation = MagicMock()
        mock_validation.is_displayable = False
        mock_validation.reason = "Data not available"

        mock_db = MagicMock()

        with patch(
            "studio.app.common.routers.dataview.DataviewService."
            "find_dataview_record",
            return_value=mock_record,
        ), patch(
            "studio.app.common.routers.dataview.RemoteSyncStatusFileUtil."
            "check_sync_status_unsynced",
            return_value=False,
        ), patch(
            "studio.app.common.routers.dataview.RemoteStorageController."
            "is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.dataview."
            "_resolve_workspace_remote_bucket_name",
            return_value="test-bucket",
        ), patch(
            "studio.app.common.routers.dataview._validate_experiment_exists_in_s3",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "studio.app.common.routers.dataview.PublishValidator."
            "validate_for_display",
            return_value=mock_validation,
        ):
            response = await public_reproduce_experiment(
                workspace_id="1", unique_id="exp123", db=mock_db
            )

        assert response.status_code == 503
        mock_db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_publish_blocks_when_s3_check_unverifiable(self):
        """Publish must block (400) when the S3 check is unverifiable (None)."""
        from unittest.mock import AsyncMock

        from studio.app.common.core.storage.remote_storage_controller import (
            RemoteStorageType,
        )
        from studio.app.common.routers.dataview import PublishFlags

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

        mock_validation = MagicMock()
        mock_validation.can_publish = True

        with patch(
            "studio.app.common.routers.dataview.DataviewService."
            "find_user_owned_dataview_record",
            return_value=mock_record,
        ), patch(
            "studio.app.common.routers.dataview.RemoteStorageType.get_activated_type",
            return_value=RemoteStorageType.S3,
        ), patch(
            "studio.app.common.routers.dataview._validate_experiment_exists_in_s3",
            new=AsyncMock(return_value=(None, "Could not verify S3 data")),
        ), patch(
            "studio.app.common.routers.dataview.PublishValidator.validate",
            return_value=mock_validation,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await publish_dataview_records(
                    id=1, flag=PublishFlags.on, db=mock_db, current_user=mock_user
                )

        assert exc_info.value.status_code == 400
        mock_db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_reproduce_synced_s3_check_fails_does_not_demote(self):
        """A transient S3 check failure (None) must not demote a synced row."""
        from unittest.mock import AsyncMock

        from studio.app.common.routers.dataview import public_reproduce_experiment

        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.local_sync_status = LocalSyncStatus.synced.value

        mock_validation = MagicMock()
        mock_validation.is_displayable = False
        mock_validation.reason = "Data not available"

        mock_db = MagicMock()

        with patch(
            "studio.app.common.routers.dataview.DataviewService."
            "find_dataview_record",
            return_value=mock_record,
        ), patch(
            "studio.app.common.routers.dataview.RemoteSyncStatusFileUtil."
            "check_sync_status_unsynced",
            return_value=False,
        ), patch(
            "studio.app.common.routers.dataview.RemoteStorageController."
            "is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.dataview."
            "_resolve_workspace_remote_bucket_name",
            return_value="test-bucket",
        ), patch(
            "studio.app.common.routers.dataview._validate_experiment_exists_in_s3",
            new=AsyncMock(return_value=(None, "Could not verify S3 data")),
        ), patch(
            "studio.app.common.routers.dataview.PublishValidator."
            "validate_for_display",
            return_value=mock_validation,
        ):
            response = await public_reproduce_experiment(
                workspace_id="1", unique_id="exp123", db=mock_db
            )

        assert response.status_code == 503
        mock_db.execute.assert_not_called()


class TestValidateExperimentExistsInS3:
    """Exercise the real tri-state S3 existence check against a mocked client."""

    @staticmethod
    def _s3_client_ctx(*, key_count=None, raise_exc=None):
        from unittest.mock import AsyncMock

        client = MagicMock()
        if raise_exc is not None:
            client.list_objects_v2 = AsyncMock(side_effect=raise_exc)
        else:
            client.list_objects_v2 = AsyncMock(return_value={"KeyCount": key_count})
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    async def _run(self, ctx):
        from studio.app.common.core.storage.s3_storage_controller import (
            S3StorageController,
        )
        from studio.app.common.routers.dataview import _validate_experiment_exists_in_s3

        with patch(
            "studio.app.common.routers.dataview.RemoteStorageController.is_available",
            return_value=True,
        ), patch.object(
            S3StorageController,
            "_S3StorageController__get_s3_client",
            return_value=ctx,
        ):
            return await _validate_experiment_exists_in_s3("1", "exp123", "test-bucket")

    @pytest.mark.asyncio
    async def test_present_returns_true(self):
        exists, error = await self._run(self._s3_client_ctx(key_count=3))
        assert exists is True
        assert error is None

    @pytest.mark.asyncio
    async def test_confirmed_absent_returns_false(self):
        exists, error = await self._run(self._s3_client_ctx(key_count=0))
        assert exists is False
        assert error is not None

    @pytest.mark.asyncio
    async def test_transient_error_returns_none(self):
        exists, error = await self._run(
            self._s3_client_ctx(raise_exc=Exception("throttled"))
        )
        assert exists is None
        assert error is not None


class TestSyncExperimentConfigForPublish:
    """Publish pre-sync helper: repair missing/stub local config from S3."""

    @staticmethod
    def _reader_ctx(controller):
        """Build an async-context-manager mock yielding `controller`."""
        from unittest.mock import AsyncMock

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=controller)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    @pytest.mark.asyncio
    async def test_noop_when_remote_unavailable(self):
        """No sync attempt when remote storage is not configured."""
        from studio.app.common.routers.dataview import (
            _sync_experiment_config_for_publish,
        )

        mock_reader = MagicMock()
        with patch(
            "studio.app.common.routers.dataview.RemoteStorageController."
            "is_available",
            return_value=False,
        ), patch("studio.app.common.routers.dataview.RemoteStorageReader", mock_reader):
            await _sync_experiment_config_for_publish("1", "uid", "test-bucket")

        mock_reader.assert_not_called()

    @pytest.mark.asyncio
    async def test_downloads_when_config_missing(self):
        """Missing local config triggers a metadata download."""
        from unittest.mock import AsyncMock

        from studio.app.common.routers.dataview import (
            _sync_experiment_config_for_publish,
        )

        controller = AsyncMock()
        with patch(
            "studio.app.common.routers.dataview.RemoteStorageController."
            "is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.dataview.os.path.exists", return_value=False
        ), patch(
            "studio.app.common.routers.dataview.RemoteStorageReader",
            return_value=self._reader_ctx(controller),
        ):
            await _sync_experiment_config_for_publish("1", "uid", "test-bucket")

        controller.download_experiment_meta.assert_awaited_once_with("1", "uid")

    @pytest.mark.asyncio
    async def test_repairs_stub_then_downloads(self):
        """A present-but-invalid stub is removed (by path), then re-downloaded."""
        from unittest.mock import AsyncMock

        from studio.app.common.routers.dataview import (
            ExptConfigReader,
            _sync_experiment_config_for_publish,
        )

        expected_path = ExptConfigReader.get_config_yaml_path("1", "uid")
        controller = AsyncMock()
        with patch(
            "studio.app.common.routers.dataview.RemoteStorageController."
            "is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.dataview.os.path.exists", return_value=True
        ), patch(
            "studio.app.common.routers.dataview.ExptConfigReader.read",
            side_effect=AssertionError("Invalid config yaml file"),
        ), patch(
            "studio.app.common.routers.dataview.os.remove"
        ) as mock_remove, patch(
            "studio.app.common.routers.dataview.RemoteStorageReader",
            return_value=self._reader_ctx(controller),
        ):
            await _sync_experiment_config_for_publish("1", "uid", "test-bucket")

        mock_remove.assert_called_once_with(expected_path)
        controller.download_experiment_meta.assert_awaited_once_with("1", "uid")

    @pytest.mark.asyncio
    async def test_stub_removal_failure_is_swallowed(self):
        """If removing the stub fails, no download runs and no error escapes."""
        from unittest.mock import AsyncMock

        from studio.app.common.routers.dataview import (
            _sync_experiment_config_for_publish,
        )

        controller = AsyncMock()
        with patch(
            "studio.app.common.routers.dataview.RemoteStorageController."
            "is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.dataview.os.path.exists", return_value=True
        ), patch(
            "studio.app.common.routers.dataview.ExptConfigReader.read",
            side_effect=AssertionError("Invalid config yaml file"),
        ), patch(
            "studio.app.common.routers.dataview.os.remove",
            side_effect=OSError("permission denied"),
        ), patch(
            "studio.app.common.routers.dataview.RemoteStorageReader",
            return_value=self._reader_ctx(controller),
        ):
            # Must not raise.
            await _sync_experiment_config_for_publish("1", "uid", "test-bucket")

        controller.download_experiment_meta.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_when_config_valid(self):
        """A valid local config is left untouched (no sync)."""
        from studio.app.common.routers.dataview import (
            _sync_experiment_config_for_publish,
        )

        mock_reader = MagicMock()
        with patch(
            "studio.app.common.routers.dataview.RemoteStorageController."
            "is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.dataview.os.path.exists", return_value=True
        ), patch(
            "studio.app.common.routers.dataview.ExptConfigReader.read",
            return_value=MagicMock(),
        ), patch(
            "studio.app.common.routers.dataview.RemoteStorageReader", mock_reader
        ):
            await _sync_experiment_config_for_publish("1", "uid", "test-bucket")

        mock_reader.assert_not_called()


class TestMultiplePublishDataviewRecords:
    """Bulk publish: pre-sync + all-or-nothing validation."""

    @staticmethod
    def _make_record(record_id):
        record = MagicMock()
        record.id = record_id
        record.workspace_id = "1"
        record.uid = f"uid{record_id}"
        record.name = f"exp{record_id}"
        return record

    @pytest.mark.asyncio
    async def test_bulk_publish_success_presyncs_each_record(self):
        """All valid: pre-sync runs per record and the service is invoked."""
        from unittest.mock import AsyncMock

        from studio.app.common.routers.dataview import (
            PublishFlags,
            multiple_publish_dataview_records,
        )

        mock_db = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 123
        mock_user.remote_bucket_name = "test-bucket"

        records = {1: self._make_record(1), 2: self._make_record(2)}

        mock_validation = MagicMock()
        mock_validation.can_publish = True

        with patch(
            "studio.app.common.routers.dataview.DataviewService."
            "find_user_owned_dataview_record",
            side_effect=lambda db, rid, uid: records.get(rid),
        ), patch(
            "studio.app.common.routers.dataview." "_sync_experiment_config_for_publish",
            new=AsyncMock(),
        ) as mock_sync, patch(
            "studio.app.common.routers.dataview.PublishValidator.validate",
            return_value=mock_validation,
        ), patch(
            "studio.app.common.routers.dataview.DataviewService."
            "multiple_publish_dataview_records"
        ) as mock_service:
            result = await multiple_publish_dataview_records(
                ids=[1, 2], flag=PublishFlags.on, db=mock_db, current_user=mock_user
            )

        assert result is True
        assert mock_sync.await_count == 2
        mock_service.assert_called_once()

    @pytest.mark.asyncio
    async def test_bulk_publish_blocks_when_a_record_invalid(self):
        """One invalid record blocks the whole batch with 400; no publish."""
        from unittest.mock import AsyncMock

        from studio.app.common.routers.dataview import (
            PublishFlags,
            multiple_publish_dataview_records,
        )

        mock_db = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 123
        mock_user.remote_bucket_name = "test-bucket"

        records = {1: self._make_record(1), 2: self._make_record(2)}

        def validate(workspace_id, unique_id, **kwargs):
            result = MagicMock()
            result.can_publish = unique_id != "uid2"
            result.reason = None if result.can_publish else "corrupted"
            return result

        with patch(
            "studio.app.common.routers.dataview.DataviewService."
            "find_user_owned_dataview_record",
            side_effect=lambda db, rid, uid: records.get(rid),
        ), patch(
            "studio.app.common.routers.dataview." "_sync_experiment_config_for_publish",
            new=AsyncMock(),
        ), patch(
            "studio.app.common.routers.dataview.PublishValidator.validate",
            side_effect=validate,
        ), patch(
            "studio.app.common.routers.dataview.DataviewService."
            "multiple_publish_dataview_records"
        ) as mock_service:
            with pytest.raises(HTTPException) as exc_info:
                await multiple_publish_dataview_records(
                    ids=[1, 2],
                    flag=PublishFlags.on,
                    db=mock_db,
                    current_user=mock_user,
                )

        assert exc_info.value.status_code == 400
        mock_service.assert_not_called()


class TestSinglePublishPreSync:
    """Single publish wires the pre-sync helper before validation."""

    @pytest.mark.asyncio
    async def test_single_publish_presyncs_before_validate(self):
        """publish_dataview_records calls the sync helper before validating."""
        from unittest.mock import AsyncMock

        from studio.app.common.routers.dataview import (
            PublishFlags,
            publish_dataview_records,
        )

        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_db.execute.return_value = mock_result

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

        mock_validation = MagicMock()
        mock_validation.can_publish = True

        order = []

        async def sync_side(*args, **kwargs):
            order.append("sync")

        def validate_side(*args, **kwargs):
            order.append("validate")
            return mock_validation

        with patch(
            "studio.app.common.routers.dataview.DataviewService."
            "find_user_owned_dataview_record",
            return_value=mock_record,
        ), patch(
            "studio.app.common.routers.dataview." "_sync_experiment_config_for_publish",
            new=AsyncMock(side_effect=sync_side),
        ) as mock_sync, patch(
            "studio.app.common.routers.dataview._resolve_workspace_remote_bucket_name",
            return_value="test-bucket",
        ), patch(
            "studio.app.common.routers.dataview.PublishValidator.validate",
            side_effect=validate_side,
        ), patch(
            "studio.app.common.routers.dataview._validate_experiment_exists_in_s3",
            new=AsyncMock(return_value=(True, None)),
        ):
            result = await publish_dataview_records(
                id=1, flag=PublishFlags.on, db=mock_db, current_user=mock_user
            )

        assert result is True
        mock_sync.assert_awaited_once_with("1", "test_uid", "test-bucket")
        # Sync must be ordered before validation.
        assert order == ["sync", "validate"]
