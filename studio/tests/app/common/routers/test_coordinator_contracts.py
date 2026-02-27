"""
Contract tests for router endpoints that use DownloadCoordinator.

Verifies that each endpoint calls the coordinator with the correct
tier, caller, and flags, and handles DownloadResult correctly.

Note: DownloadCoordinator is lazily imported inside function bodies in all
routers, so we patch it at its source module path, not the consuming module.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from studio.app.common.core.storage.sync_tier import DownloadResult, SyncTier

# Canonical patch path for DownloadCoordinator (lazily imported everywhere)
_DC_PATH = "studio.app.common.core.storage.download_coordinator.DownloadCoordinator"


def _mock_coordinator(**method_overrides):
    """Create a mock DownloadCoordinator with patched get_instance."""
    coordinator = MagicMock()
    for name, mock_fn in method_overrides.items():
        setattr(coordinator, name, mock_fn)
    return coordinator


# ---------------------------------------------------------------------------
# outputs.py contract tests
# ---------------------------------------------------------------------------


class TestSyncVisualizationFilesContract:
    """sync_visualization_files should use coordinator with VISUALIZATION tier."""

    @pytest.mark.asyncio
    async def test_calls_coordinator_with_visualization_tier(self):
        from studio.app.common.routers.outputs import sync_visualization_files

        mock_result = DownloadResult(success=True, achieved_tier=SyncTier.VISUALIZATION)

        with patch(
            "studio.app.common.routers.outputs.RemoteStorageController"
        ) as mock_rsc:
            mock_rsc.is_available.return_value = True

            with patch(_DC_PATH) as mock_dc_cls:
                mock_coordinator = MagicMock()
                mock_coordinator.ensure_synced = AsyncMock(return_value=mock_result)
                mock_dc_cls.get_instance.return_value = mock_coordinator

                with patch(
                    "studio.app.common.routers.outputs._download_input_files",
                    new_callable=AsyncMock,
                ):
                    mock_bg = MagicMock()
                    result = await sync_visualization_files(
                        "ws1", "uid1", mock_bg, "bucket"
                    )

        assert result is True
        mock_coordinator.ensure_synced.assert_called_once()
        call_kwargs = mock_coordinator.ensure_synced.call_args.kwargs
        assert call_kwargs["required_tier"] == SyncTier.VISUALIZATION
        assert call_kwargs["caller"] == "outputs_viz"

    @pytest.mark.asyncio
    async def test_returns_true_when_no_remote_storage(self):
        from studio.app.common.routers.outputs import sync_visualization_files

        with patch(
            "studio.app.common.routers.outputs.RemoteStorageController"
        ) as mock_rsc:
            mock_rsc.is_available.return_value = False

            mock_bg = MagicMock()
            result = await sync_visualization_files("bucket", "ws1", "uid1", mock_bg)

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_download_failure(self):
        from studio.app.common.routers.outputs import sync_visualization_files

        mock_result = DownloadResult(
            success=False, achieved_tier=SyncTier.NONE, error="S3 error"
        )

        with patch(
            "studio.app.common.routers.outputs.RemoteStorageController"
        ) as mock_rsc:
            mock_rsc.is_available.return_value = True

            with patch(_DC_PATH) as mock_dc_cls:
                mock_coordinator = MagicMock()
                mock_coordinator.ensure_synced = AsyncMock(return_value=mock_result)
                mock_dc_cls.get_instance.return_value = mock_coordinator

                mock_bg = MagicMock()
                result = await sync_visualization_files(
                    "bucket", "ws1", "uid1", mock_bg
                )

        assert result is False


class TestBackgroundFullSyncContract:
    """_background_full_sync should use coordinator with ALL tier + exclusive lock."""

    @pytest.mark.asyncio
    async def test_calls_coordinator_with_all_tier_exclusive(self):
        from studio.app.common.routers.outputs import _background_full_sync

        mock_result = DownloadResult(success=True, achieved_tier=SyncTier.ALL)

        with patch(_DC_PATH) as mock_dc_cls:
            mock_coordinator = MagicMock()
            mock_coordinator.ensure_synced = AsyncMock(return_value=mock_result)
            mock_dc_cls.get_instance.return_value = mock_coordinator

            await _background_full_sync("bucket", "ws1", "uid1")

        call_kwargs = mock_coordinator.ensure_synced.call_args.kwargs
        assert call_kwargs["required_tier"] == SyncTier.ALL
        assert call_kwargs["use_exclusive_lock"] is True
        assert call_kwargs["update_db_status"] is True
        assert call_kwargs["caller"] == "bg_full_sync"


class TestEnsureVisualizationSyncedContract:
    """_ensure_visualization_synced extracts IDs and calls coordinator."""

    @pytest.mark.asyncio
    async def test_no_remote_storage_returns_early(self):
        from studio.app.common.routers.outputs import _ensure_visualization_synced

        with patch(
            "studio.app.common.routers.outputs.RemoteStorageController"
        ) as mock_rsc:
            mock_rsc.is_available.return_value = False
            await _ensure_visualization_synced("/some/path", "bucket")


class TestGetThumbnailContract:
    """get_thumbnail should use coordinator with THUMBNAILS_ONLY tier."""

    @pytest.mark.asyncio
    async def test_calls_coordinator_for_missing_thumbnail(self):
        from studio.app.common.routers.outputs import get_thumbnail

        mock_result = DownloadResult(
            success=True, achieved_tier=SyncTier.THUMBNAILS_ONLY
        )

        with patch(
            "studio.app.common.routers.outputs.RemoteStorageController"
        ) as mock_rsc:
            mock_rsc.is_available.return_value = True

            with patch(
                "studio.app.common.routers.outputs.os.path.exists"
            ) as mock_exists:
                mock_exists.return_value = False

                with patch(_DC_PATH) as mock_dc_cls:
                    mock_coordinator = MagicMock()
                    mock_coordinator.ensure_synced = AsyncMock(return_value=mock_result)
                    mock_dc_cls.get_instance.return_value = mock_coordinator

                    with patch(
                        "studio.app.common.routers.outputs._get_thumbnail_png_path",
                        return_value="/fake/thumb.png",
                    ):
                        try:
                            await get_thumbnail("ws1", "uid1", "input", "bucket")
                        except Exception:
                            pass  # May raise 404

                calls = mock_coordinator.ensure_synced.call_args_list
                assert len(calls) >= 1
                first_call = calls[0].kwargs
                assert first_call["required_tier"] == SyncTier.THUMBNAILS_ONLY
                assert first_call["caller"] == "thumbnail"


class TestEnsureInputFileSyncedContract:
    """_ensure_input_file_synced calls RemoteStorageController directly."""

    @pytest.mark.asyncio
    async def test_returns_true_when_file_exists_locally(self):
        from studio.app.common.routers.outputs import _ensure_input_file_synced

        with patch(
            "studio.app.common.routers.outputs.os.path.exists", return_value=True
        ):
            result = await _ensure_input_file_synced("ws1", "data.tiff", "bucket")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_no_remote_storage(self):
        from studio.app.common.routers.outputs import _ensure_input_file_synced

        with patch(
            "studio.app.common.routers.outputs.os.path.exists", return_value=False
        ):
            with patch(
                "studio.app.common.routers.outputs.RemoteStorageController"
            ) as mock_rsc:
                mock_rsc.is_available.return_value = False
                result = await _ensure_input_file_synced("ws1", "data.tiff", "bucket")

        assert result is False

    @pytest.mark.asyncio
    async def test_raises_503_on_s3_error(self):
        from fastapi import HTTPException

        from studio.app.common.routers.outputs import _ensure_input_file_synced

        with patch(
            "studio.app.common.routers.outputs.os.path.exists", return_value=False
        ):
            with patch(
                "studio.app.common.routers.outputs.RemoteStorageController"
            ) as mock_rsc:
                mock_rsc.is_available.return_value = True
                mock_controller = MagicMock()
                mock_controller.download_input_data = AsyncMock(
                    side_effect=Exception("S3 error")
                )
                mock_rsc.return_value = mock_controller

                with pytest.raises(HTTPException) as exc_info:
                    await _ensure_input_file_synced("ws1", "data.tiff", "bucket")

                assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# experiment.py contract tests
# ---------------------------------------------------------------------------


class TestSyncRemoteExperimentContract:
    """sync_remote_experiment should use coordinator with ALL + exclusive lock."""

    @pytest.mark.asyncio
    async def test_calls_coordinator_with_correct_params(self):
        from studio.app.common.routers.experiment import sync_remote_experiment

        mock_result = DownloadResult(success=True, achieved_tier=SyncTier.ALL)

        with patch(_DC_PATH) as mock_dc_cls:
            mock_coordinator = MagicMock()
            mock_coordinator.ensure_synced = AsyncMock(return_value=mock_result)
            mock_dc_cls.get_instance.return_value = mock_coordinator

            result = await sync_remote_experiment("ws1", "uid1", "bucket")

        assert result is True
        call_kwargs = mock_coordinator.ensure_synced.call_args.kwargs
        assert call_kwargs["required_tier"] == SyncTier.ALL
        assert call_kwargs["use_exclusive_lock"] is True
        assert call_kwargs["update_db_status"] is True
        assert call_kwargs["caller"] == "user_sync_remote"

    @pytest.mark.asyncio
    async def test_raises_404_on_failure(self):
        from fastapi import HTTPException

        from studio.app.common.routers.experiment import sync_remote_experiment

        mock_result = DownloadResult(
            success=False, achieved_tier=SyncTier.NONE, error="not found"
        )

        with patch(_DC_PATH) as mock_dc_cls:
            mock_coordinator = MagicMock()
            mock_coordinator.ensure_synced = AsyncMock(return_value=mock_result)
            mock_dc_cls.get_instance.return_value = mock_coordinator

            with pytest.raises(HTTPException) as exc_info:
                await sync_remote_experiment("ws1", "uid1", "bucket")
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_423_on_lock_held(self):
        from fastapi import HTTPException

        from studio.app.common.routers.experiment import sync_remote_experiment

        mock_result = DownloadResult(
            success=False,
            achieved_tier=SyncTier.NONE,
            error="lock is held",
            is_lock_error=True,
        )

        with patch(_DC_PATH) as mock_dc_cls:
            mock_coordinator = MagicMock()
            mock_coordinator.ensure_synced = AsyncMock(return_value=mock_result)
            mock_dc_cls.get_instance.return_value = mock_coordinator

            with pytest.raises(HTTPException) as exc_info:
                await sync_remote_experiment("ws1", "uid1", "bucket")
            assert exc_info.value.status_code == 423


class TestGetExperimentsCoordinatorContract:
    """get_experiments should use coordinator for per-experiment metadata sync."""

    @pytest.mark.asyncio
    async def test_calls_coordinator_when_local_configs_exist(self):
        from studio.app.common.routers.experiment import get_experiments

        with patch(
            "studio.app.common.routers.experiment.RemoteStorageController"
        ) as mock_rsc:
            mock_rsc.is_available.return_value = True

            with patch("studio.app.common.routers.experiment.glob") as mock_glob:
                mock_glob.return_value = ["/some/experiment.yaml"]

                with patch(_DC_PATH) as mock_dc_cls:
                    mock_coordinator = MagicMock()
                    mock_coordinator.ensure_metadata_available = AsyncMock()
                    mock_dc_cls.get_instance.return_value = mock_coordinator

                    with patch(
                        "studio.app.common.routers.experiment.ExptConfigReader"
                    ) as mock_reader:
                        mock_reader.get_config_yaml_wild_path.return_value = (
                            "/fake/path/*.yaml"
                        )
                        mock_reader.return_value.read.return_value = MagicMock()

                        mock_db = MagicMock()
                        await get_experiments("ws1", mock_db, "bucket")

                mock_coordinator.ensure_metadata_available.assert_called_once()
                call_kwargs = (
                    mock_coordinator.ensure_metadata_available.call_args.kwargs
                )
                assert call_kwargs["workspace_id"] == "ws1"
                assert call_kwargs["caller"] == "records_page"


# ---------------------------------------------------------------------------
# internal.py contract tests
# ---------------------------------------------------------------------------


class TestDownloadSingleExperimentContract:
    """_download_single_experiment should use coordinator with tiered approach."""

    @pytest.mark.asyncio
    async def test_calls_coordinator_with_thumbnails_then_essential(self):
        from studio.app.common.routers.internal import _download_single_experiment

        call_tiers = []

        async def mock_ensure_synced(**kwargs):
            call_tiers.append(kwargs["required_tier"])
            return DownloadResult(success=True, achieved_tier=kwargs["required_tier"])

        with patch(_DC_PATH) as mock_dc_cls:
            mock_coordinator = MagicMock()
            mock_coordinator.ensure_synced = AsyncMock(side_effect=mock_ensure_synced)
            mock_dc_cls.get_instance.return_value = mock_coordinator

            await _download_single_experiment(
                "bucket", "ws1", "uid1", has_thumbnails=True
            )

        assert SyncTier.THUMBNAILS_ONLY in call_tiers
        assert SyncTier.ESSENTIAL_ONLY in call_tiers

    @pytest.mark.asyncio
    async def test_skips_thumbnails_when_not_needed(self):
        from studio.app.common.routers.internal import _download_single_experiment

        call_tiers = []

        async def mock_ensure_synced(**kwargs):
            call_tiers.append(kwargs["required_tier"])
            return DownloadResult(success=True, achieved_tier=kwargs["required_tier"])

        with patch(_DC_PATH) as mock_dc_cls:
            mock_coordinator = MagicMock()
            mock_coordinator.ensure_synced = AsyncMock(side_effect=mock_ensure_synced)
            mock_dc_cls.get_instance.return_value = mock_coordinator

            await _download_single_experiment(
                "bucket", "ws1", "uid1", has_thumbnails=False
            )

        assert SyncTier.THUMBNAILS_ONLY not in call_tiers
        assert SyncTier.ESSENTIAL_ONLY in call_tiers


class TestDownloadExperimentsForUserContract:
    """_download_experiments_for_user should use coordinator batch."""

    @pytest.mark.asyncio
    async def test_calls_batch_with_metadata_tier(self):
        from studio.app.common.routers.internal import _download_experiments_for_user

        with patch(
            "studio.app.common.routers.internal.RemoteStorageController"
        ) as mock_rsc:
            mock_rsc.is_available.return_value = True

            with patch(_DC_PATH) as mock_dc_cls:
                mock_coordinator = MagicMock()
                mock_coordinator.ensure_synced_batch = AsyncMock(return_value={})
                mock_dc_cls.get_instance.return_value = mock_coordinator

                with patch(
                    "studio.app.common.routers.internal.get_session"
                ) as mock_get_session:
                    mock_db = MagicMock()
                    mock_get_session.return_value = mock_db

                    mock_workspace = MagicMock()
                    mock_workspace.id = "ws1"
                    exec_result = mock_db.execute.return_value
                    exec_result.scalars.return_value.all.return_value = [mock_workspace]

                    mock_db.query.return_value.filter.return_value.all.return_value = [
                        ("uid1",),
                        ("uid2",),
                    ]

                    await _download_experiments_for_user("bucket", 1)

                mock_coordinator.ensure_synced_batch.assert_called_once()
                call_kwargs = mock_coordinator.ensure_synced_batch.call_args.kwargs
                assert call_kwargs["required_tier"] == SyncTier.METADATA_ONLY
                assert call_kwargs["caller"] == "user_migration"


# ---------------------------------------------------------------------------
# experiment_reader.py contract tests
# ---------------------------------------------------------------------------


class TestExptConfigReaderEnsureSyncedContract:
    """ExptConfigReader.ensure_synced_async should use coordinator."""

    @pytest.mark.asyncio
    async def test_calls_coordinator_with_metadata_tier(self):
        from studio.app.common.core.experiment.experiment_reader import ExptConfigReader

        mock_result = DownloadResult(success=True, achieved_tier=SyncTier.METADATA_ONLY)

        with patch.object(
            ExptConfigReader,
            "get_config_yaml_path",
            return_value="/fake/experiment.yaml",
        ):
            with patch("os.path.exists", return_value=False):
                with patch(
                    "studio.app.common.core.storage.remote_storage_controller."
                    "RemoteStorageController"
                ) as mock_rsc:
                    mock_rsc.is_available.return_value = True

                    with patch(_DC_PATH) as mock_dc_cls:
                        mock_coordinator = MagicMock()
                        mock_coordinator.ensure_synced = AsyncMock(
                            return_value=mock_result
                        )
                        mock_dc_cls.get_instance.return_value = mock_coordinator

                        await ExptConfigReader.ensure_synced_async(
                            "ws1", "uid1", "default-bucket"
                        )

                    call_kwargs = mock_coordinator.ensure_synced.call_args.kwargs
                    assert call_kwargs["required_tier"] == SyncTier.METADATA_ONLY
                    assert call_kwargs["caller"] == "config_reader"
