"""
Tests for experiment router per-experiment metadata sync behavior.

Covers Issue C: Records page hides published experiments when any local
experiment exists. Verifies per-experiment comparison logic in get_experiments().
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from studio.app.common.routers.experiment import get_experiments


def _make_config(workspace_id, unique_id, name="test"):
    """Create a minimal ExptConfig for testing."""
    from studio.app.common.core.experiment.experiment import ExptConfig

    return ExptConfig(
        workspace_id=workspace_id,
        unique_id=unique_id,
        name=name,
        started_at="2024-01-01",
        finished_at="2024-01-01",
        success=1,
        hasNWB=False,
        function={},
        procs=None,
        nwb=None,
        snakemake=None,
        data_usage=None,
        timezone=None,
    )


class TestGetExperimentsMetadataSync:
    """Tests for per-experiment metadata sync in get_experiments."""

    @pytest.mark.asyncio
    async def test_downloads_missing_experiment_metadata(self):
        """Downloads metadata for DB-published experiments missing locally."""
        mock_db = MagicMock()
        mock_controller = AsyncMock()

        config = _make_config("ws1", "uid1")

        with patch(
            "studio.app.common.routers.experiment.RemoteStorageController.is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.experiment.glob",
            side_effect=[
                # First glob: one local config exists
                ["/data/output/ws1/uid1/experiment.yaml"],
                # Second glob after download: now has both
                [
                    "/data/output/ws1/uid1/experiment.yaml",
                    "/data/output/ws1/uid2/experiment.yaml",
                ],
            ],
        ), patch(
            "studio.app.common.routers.experiment"
            ".ExptConfigReader.get_config_yaml_wild_path",
            return_value="/data/output/ws1/*/experiment.yaml",
        ), patch(
            "studio.app.common.routers.experiment"
            ".ExptConfigReader.get_local_experiment_uids",
            return_value={"uid1"},
        ), patch(
            "studio.app.common.routers.experiment._get_published_uids",
            return_value={"uid1", "uid2"},
        ), patch(
            "studio.app.common.routers.experiment.RemoteStorageReader"
        ) as mock_reader_cls, patch(
            "studio.app.common.routers.experiment.ExptConfigReader.read_from_path",
            return_value=config,
        ), patch(
            "studio.app.common.routers.experiment.RemoteSyncStatusFileUtil."
            "check_sync_status_success",
            return_value=True,
        ), patch(
            "studio.app.common.routers.experiment.ExptConfigReader."
            "validate_experiment_config",
            return_value=True,
        ):
            mock_reader_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_controller
            )
            mock_reader_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await get_experiments(
                workspace_id="ws1",
                db=mock_db,
                remote_bucket_name="bucket1",
            )

        # Should have called download_experiment_meta for the missing uid
        mock_controller.download_experiment_meta.assert_called_once_with("ws1", "uid2")

    @pytest.mark.asyncio
    async def test_continues_on_sync_exception(self):
        """get_experiments continues when per-experiment sync raises."""
        mock_db = MagicMock()

        config = _make_config("ws1", "uid1")

        with patch(
            "studio.app.common.routers.experiment.RemoteStorageController.is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.experiment.glob",
            return_value=["/data/output/ws1/uid1/experiment.yaml"],
        ), patch(
            "studio.app.common.routers.experiment"
            ".ExptConfigReader.get_config_yaml_wild_path",
            return_value="/data/output/ws1/*/experiment.yaml",
        ), patch(
            "studio.app.common.routers.experiment"
            ".ExptConfigReader.get_local_experiment_uids",
            side_effect=RuntimeError("db explosion"),
        ), patch(
            "studio.app.common.routers.experiment.ExptConfigReader.read_from_path",
            return_value=config,
        ), patch(
            "studio.app.common.routers.experiment.RemoteSyncStatusFileUtil."
            "check_sync_status_success",
            return_value=True,
        ), patch(
            "studio.app.common.routers.experiment.ExptConfigReader."
            "validate_experiment_config",
            return_value=True,
        ), patch(
            "studio.app.common.routers.experiment.logger.warning",
        ):
            # Should not raise despite sync failure
            result = await get_experiments(
                workspace_id="ws1",
                db=mock_db,
                remote_bucket_name="bucket1",
            )

        # Should still return the locally available experiments
        assert "uid1" in result

    @pytest.mark.asyncio
    async def test_skips_sync_when_no_remote_storage(self):
        """get_experiments skips sync when remote storage unavailable."""
        mock_db = MagicMock()

        config = _make_config("ws1", "uid1")

        with patch(
            "studio.app.common.routers.experiment.RemoteStorageController.is_available",
            return_value=False,
        ), patch(
            "studio.app.common.routers.experiment.glob",
            return_value=["/data/output/ws1/uid1/experiment.yaml"],
        ), patch(
            "studio.app.common.routers.experiment"
            ".ExptConfigReader.get_config_yaml_wild_path",
            return_value="/data/output/ws1/*/experiment.yaml",
        ), patch(
            "studio.app.common.routers.experiment.ExptConfigReader.read_from_path",
            return_value=config,
        ), patch(
            "studio.app.common.routers.experiment.ExptConfigReader."
            "validate_experiment_config",
            return_value=True,
        ):
            result = await get_experiments(
                workspace_id="ws1",
                db=mock_db,
                remote_bucket_name="bucket1",
            )

        assert "uid1" in result

    @pytest.mark.asyncio
    async def test_no_download_when_all_present(self):
        """No download triggered when all published experiments exist locally."""
        mock_db = MagicMock()

        config = _make_config("ws1", "uid1")

        with patch(
            "studio.app.common.routers.experiment.RemoteStorageController.is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.experiment.glob",
            return_value=["/data/output/ws1/uid1/experiment.yaml"],
        ), patch(
            "studio.app.common.routers.experiment"
            ".ExptConfigReader.get_config_yaml_wild_path",
            return_value="/data/output/ws1/*/experiment.yaml",
        ), patch(
            "studio.app.common.routers.experiment"
            ".ExptConfigReader.get_local_experiment_uids",
            return_value={"uid1"},
        ), patch(
            "studio.app.common.routers.experiment._get_published_uids",
            return_value={"uid1"},
        ), patch(
            "studio.app.common.routers.experiment.RemoteStorageSimpleReader"
        ) as mock_reader_cls, patch(
            "studio.app.common.routers.experiment.ExptConfigReader.read_from_path",
            return_value=config,
        ), patch(
            "studio.app.common.routers.experiment.RemoteSyncStatusFileUtil."
            "check_sync_status_success",
            return_value=True,
        ), patch(
            "studio.app.common.routers.experiment.ExptConfigReader."
            "validate_experiment_config",
            return_value=True,
        ):
            result = await get_experiments(
                workspace_id="ws1",
                db=mock_db,
                remote_bucket_name="bucket1",
            )

        # RemoteStorageSimpleReader should NOT have been instantiated
        mock_reader_cls.assert_not_called()
        assert "uid1" in result

    @pytest.mark.asyncio
    async def test_empty_config_paths_downloads_all(self):
        """Falls back to download_all_experiments_metas when no local configs."""
        mock_db = MagicMock()
        mock_controller = AsyncMock()

        config = _make_config("ws1", "uid1")

        with patch(
            "studio.app.common.routers.experiment.RemoteStorageController.is_available",
            return_value=True,
        ), patch(
            "studio.app.common.routers.experiment.glob",
            side_effect=[
                [],  # First glob: no local configs
                ["/data/output/ws1/uid1/experiment.yaml"],  # After download
            ],
        ), patch(
            "studio.app.common.routers.experiment"
            ".ExptConfigReader.get_config_yaml_wild_path",
            return_value="/data/output/ws1/*/experiment.yaml",
        ), patch(
            "studio.app.common.routers.experiment.RemoteStorageSimpleReader"
        ) as mock_reader_cls, patch(
            "studio.app.common.routers.experiment.ExptConfigReader.read_from_path",
            return_value=config,
        ), patch(
            "studio.app.common.routers.experiment.RemoteSyncStatusFileUtil."
            "check_sync_status_success",
            return_value=True,
        ), patch(
            "studio.app.common.routers.experiment.ExptConfigReader."
            "validate_experiment_config",
            return_value=True,
        ):
            mock_reader_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_controller
            )
            mock_reader_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await get_experiments(
                workspace_id="ws1",
                db=mock_db,
                remote_bucket_name="bucket1",
            )

        # Should call download_all_experiments_metas (not per-experiment)
        mock_controller.download_all_experiments_metas.assert_called_once_with(["ws1"])
