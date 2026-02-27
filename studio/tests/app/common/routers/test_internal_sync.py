"""
Tests for internal router sync functions.

Covers gap #10: _download_experiments_for_user() RemoteStorageSimpleReader fallback
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from studio.app.common.routers.internal import _download_experiments_for_user


class TestDownloadExperimentsForUser:
    """Tests for _download_experiments_for_user background task."""

    @pytest.mark.asyncio
    async def test_skips_when_remote_storage_unavailable(self):
        """No-op when remote storage is not available."""
        with patch(
            "studio.app.common.routers.internal.RemoteStorageController.is_available",
            return_value=False,
        ):
            await _download_experiments_for_user("bucket1", 123)

    @pytest.mark.asyncio
    async def test_skips_when_no_workspaces(self):
        """No-op when user has no workspaces."""
        mock_db = MagicMock()
        mock_db.execute.return_value.scalars.return_value.all.return_value = []

        with patch(
            "studio.app.common.routers.internal.RemoteStorageController.is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.internal.get_session",
            return_value=mock_db,
        ):
            await _download_experiments_for_user("bucket1", 123)

        mock_db.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_coordinator_for_db_experiments(self):
        """Downloads metadata via coordinator for workspaces with DB records."""
        mock_db = MagicMock()
        mock_ws = MagicMock()
        mock_ws.id = 1
        mock_db.execute.return_value.scalars.return_value.all.return_value = [mock_ws]
        mock_db.query.return_value.filter.return_value.all.return_value = [
            ("uid1",),
            ("uid2",),
        ]

        mock_coordinator = MagicMock()
        mock_coordinator.ensure_synced_batch = AsyncMock(return_value={})

        with patch(
            "studio.app.common.routers.internal.RemoteStorageController.is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.internal.get_session",
            return_value=mock_db,
        ), patch(
            "studio.app.common.core.storage.download_coordinator."
            "DownloadCoordinator.get_instance",
            return_value=mock_coordinator,
        ):
            await _download_experiments_for_user("bucket1", 123)

        mock_coordinator.ensure_synced_batch.assert_called_once()
        call_kwargs = mock_coordinator.ensure_synced_batch.call_args[1]
        assert len(call_kwargs["experiments"]) == 2
        mock_db.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_to_remote_reader_when_no_db_records(self):
        """Uses RemoteStorageSimpleReader for workspaces
        without DB records (Gap #10).
        """
        mock_db = MagicMock()
        mock_ws = MagicMock()
        mock_ws.id = 1
        mock_db.execute.return_value.scalars.return_value.all.return_value = [mock_ws]
        # No DB records for this workspace
        mock_db.query.return_value.filter.return_value.all.return_value = []

        mock_reader = AsyncMock()
        mock_reader.__aenter__ = AsyncMock(return_value=mock_reader)
        mock_reader.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "studio.app.common.routers.internal.RemoteStorageController.is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.internal.get_session",
            return_value=mock_db,
        ), patch(
            "studio.app.common.routers.internal.RemoteStorageSimpleReader",
            return_value=mock_reader,
        ):
            await _download_experiments_for_user("bucket1", 123)

        mock_reader.download_all_experiments_metas.assert_called_once_with(["1"])
        mock_db.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_exception_gracefully(self):
        """Exceptions are caught and db is closed."""
        mock_db = MagicMock()

        with patch(
            "studio.app.common.routers.internal.RemoteStorageController.is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.internal.get_session",
            return_value=mock_db,
        ):
            mock_db.execute.side_effect = RuntimeError("DB connection lost")
            # Should not raise
            await _download_experiments_for_user("bucket1", 123)

        mock_db.close.assert_called_once()
