"""
Tests for experiment router sync-related behavior.

Covers gap #11: get_experiments() graceful degradation when
coordinator.ensure_metadata_available() raises.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from studio.app.common.routers.experiment import get_experiments


class TestGetExperimentsMetadataSync:
    """Tests for per-experiment metadata sync in get_experiments (Gap #11)."""

    @pytest.mark.asyncio
    async def test_continues_on_coordinator_exception(self):
        """get_experiments continues when ensure_metadata_available raises."""
        from studio.app.common.core.experiment.experiment import ExptConfig

        mock_db = MagicMock()

        config = ExptConfig(
            workspace_id="ws1",
            unique_id="uid1",
            name="test",
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
            "studio.app.common.core.storage.download_coordinator."
            "DownloadCoordinator.get_instance"
        ) as mock_get_instance, patch(
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
            mock_coordinator = MagicMock()
            mock_coordinator.ensure_metadata_available = AsyncMock(
                side_effect=RuntimeError("coordinator explosion")
            )
            mock_get_instance.return_value = mock_coordinator

            # Should not raise despite coordinator failure
            result = await get_experiments(
                workspace_id="ws1",
                db=mock_db,
                remote_bucket_name="bucket1",
            )

        # Should still return the locally available experiments
        assert "uid1" in result

    @pytest.mark.asyncio
    async def test_skips_coordinator_when_no_remote_storage(self):
        """get_experiments skips coordinator when remote storage unavailable."""
        from studio.app.common.core.experiment.experiment import ExptConfig

        mock_db = MagicMock()

        config = ExptConfig(
            workspace_id="ws1",
            unique_id="uid1",
            name="test",
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
