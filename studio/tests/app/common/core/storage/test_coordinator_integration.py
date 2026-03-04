"""
System/integration tests for the coordinator startup flow and sync_job updates.

Tests:
- Startup leader election + DownloadCoordinator initialization
- Updated sync_job integration with DownloadCoordinator
- Staleness spot-check in validation job
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from studio.app.common.core.storage.sync_tier import SyncTier


class TestStartupSyncWithCoordinator:
    """sync_job.run_startup_sync now uses DownloadCoordinator."""

    @pytest.mark.asyncio
    async def test_startup_sync_uses_coordinator_batch(self):
        """Startup sync should call coordinator.ensure_synced_batch."""
        from studio.app.common.core.background.sync_job import (
            PublishedExperimentSyncJob,
        )

        published = [
            ("ws1", "uid1", 1, "bucket1"),
            ("ws1", "uid2", 2, "bucket1"),
        ]

        with patch.object(
            PublishedExperimentSyncJob,
            "_get_all_published_experiments",
            return_value=published,
        ):
            with patch("os.path.exists", return_value=False):
                with patch(
                    "studio.app.common.core.storage"
                    ".download_coordinator"
                    ".DownloadCoordinator"
                ) as mock_dc_cls:
                    mock_coordinator = MagicMock()
                    mock_coordinator.ensure_synced_batch = AsyncMock(return_value={})
                    mock_dc_cls.get_instance.return_value = mock_coordinator

                    await PublishedExperimentSyncJob.run_startup_sync()

        # Should be called twice: once for THUMBNAILS, once for ESSENTIAL
        assert mock_coordinator.ensure_synced_batch.call_count == 2

        calls = mock_coordinator.ensure_synced_batch.call_args_list

        # First call: thumbnails
        first_kwargs = calls[0].kwargs
        assert first_kwargs["required_tier"] == SyncTier.THUMBNAILS_ONLY
        assert first_kwargs["caller"] == "startup_sync"

        # Second call: essential
        second_kwargs = calls[1].kwargs
        assert second_kwargs["required_tier"] == SyncTier.ESSENTIAL_ONLY
        assert second_kwargs["caller"] == "startup_sync"

    @pytest.mark.asyncio
    async def test_startup_sync_groups_by_bucket(self):
        """Experiments are grouped by bucket for batch processing."""
        from studio.app.common.core.background.sync_job import (
            PublishedExperimentSyncJob,
        )

        published = [
            ("ws1", "uid1", 1, "bucket-a"),
            ("ws1", "uid2", 2, "bucket-b"),
            ("ws2", "uid3", 3, "bucket-a"),
        ]

        with patch.object(
            PublishedExperimentSyncJob,
            "_get_all_published_experiments",
            return_value=published,
        ):
            with patch("os.path.exists", return_value=False):
                with patch(
                    "studio.app.common.core.storage"
                    ".download_coordinator"
                    ".DownloadCoordinator"
                ) as mock_dc_cls:
                    mock_coordinator = MagicMock()
                    mock_coordinator.ensure_synced_batch = AsyncMock(return_value={})
                    mock_dc_cls.get_instance.return_value = mock_coordinator

                    await PublishedExperimentSyncJob.run_startup_sync()

        # 2 buckets x 2 phases = 4 calls
        assert mock_coordinator.ensure_synced_batch.call_count == 4

        # Verify bucket names
        bucket_names = [
            call.kwargs["bucket_name"]
            for call in mock_coordinator.ensure_synced_batch.call_args_list
        ]
        assert "bucket-a" in bucket_names
        assert "bucket-b" in bucket_names

    @pytest.mark.asyncio
    async def test_startup_sync_skips_locally_present(self):
        """Startup sync skips experiments that exist locally."""
        from studio.app.common.core.background.sync_job import (
            PublishedExperimentSyncJob,
        )

        published = [
            ("ws1", "uid1", 1, "bucket1"),
        ]

        with patch.object(
            PublishedExperimentSyncJob,
            "_get_all_published_experiments",
            return_value=published,
        ):
            with patch("os.path.exists", return_value=True):
                with patch(
                    "studio.app.common.core.storage"
                    ".download_coordinator"
                    ".DownloadCoordinator"
                ) as mock_dc_cls:
                    mock_coordinator = MagicMock()
                    mock_coordinator.ensure_synced_batch = AsyncMock(return_value={})
                    mock_dc_cls.get_instance.return_value = mock_coordinator

                    await PublishedExperimentSyncJob.run_startup_sync()

        # All present locally, no downloads
        mock_coordinator.ensure_synced_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_startup_sync_handles_empty_published(self):
        """No published experiments = no coordinator calls."""
        from studio.app.common.core.background.sync_job import (
            PublishedExperimentSyncJob,
        )

        with patch.object(
            PublishedExperimentSyncJob,
            "_get_all_published_experiments",
            return_value=[],
        ):
            await PublishedExperimentSyncJob.run_startup_sync()


class TestValidationJobSpotCheck:
    """Periodic validation job includes staleness spot-check."""

    @pytest.mark.asyncio
    async def test_spot_check_runs_on_api_containers(self):
        """Spot-check should run when IS_BACKGROUND_SERVICE != '1'."""
        from studio.app.common.core.background.sync_job import (
            PublishedExperimentSyncJob,
        )

        with patch.object(
            PublishedExperimentSyncJob, "_run_validation_logic", new_callable=AsyncMock
        ):
            with patch.dict("os.environ", {"IS_BACKGROUND_SERVICE": "0"}):
                with patch(
                    "studio.app.common.core.storage.sync_state_tracker.SyncStateTracker"
                ) as mock_tracker:
                    mock_tracker.check_synced_staleness_spot_check.return_value = 0

                    await PublishedExperimentSyncJob.run()

                    spot_check = mock_tracker.check_synced_staleness_spot_check
                    spot_check.assert_called_once_with(sample_size=10)

    @pytest.mark.asyncio
    async def test_spot_check_skipped_on_background_service(self):
        """Spot-check should NOT run on background service containers."""
        from studio.app.common.core.background.sync_job import (
            PublishedExperimentSyncJob,
        )

        with patch.object(
            PublishedExperimentSyncJob, "_run_validation_logic", new_callable=AsyncMock
        ):
            with patch.dict("os.environ", {"IS_BACKGROUND_SERVICE": "1"}):
                with patch(
                    "studio.app.common.core.storage.sync_state_tracker.SyncStateTracker"
                ) as mock_tracker:
                    await PublishedExperimentSyncJob.run()

                    mock_tracker.check_synced_staleness_spot_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_spot_check_exception_doesnt_crash_validation(self):
        """Spot-check exceptions are caught and don't affect validation."""
        from studio.app.common.core.background.sync_job import (
            PublishedExperimentSyncJob,
        )

        with patch.object(
            PublishedExperimentSyncJob, "_run_validation_logic", new_callable=AsyncMock
        ):
            with patch.dict("os.environ", {"IS_BACKGROUND_SERVICE": "0"}):
                with patch(
                    "studio.app.common.core.storage.sync_state_tracker.SyncStateTracker"
                ) as mock_tracker:
                    mock_tracker.check_synced_staleness_spot_check.side_effect = (
                        Exception("DB error")
                    )

                    # Should not raise
                    await PublishedExperimentSyncJob.run()


class TestCoordinatorConcurrencyContract:
    """System-level test: concurrent ensure_synced calls are deduplicated."""

    @pytest.mark.asyncio
    async def test_concurrent_same_experiment_deduplicates(self):
        """Multiple concurrent calls for same experiment should dedup."""
        from studio.app.common.core.storage.download_coordinator import (
            DownloadCoordinator,
        )
        from studio.app.common.core.storage.sync_state_tracker import SyncProbeResult

        DownloadCoordinator._instance = None
        coordinator = DownloadCoordinator.initialize()

        download_count = 0

        async def mock_download_standard(bucket, ws, uid, tier):
            nonlocal download_count
            download_count += 1
            await asyncio.sleep(0.05)  # Simulate download time
            return tier

        with patch(
            "studio.app.common.core.storage.download_coordinator."
            "RemoteStorageController"
        ) as mock_rsc:
            mock_rsc.is_available.return_value = True

            with patch.object(coordinator, "_check_disk_space", return_value=True):
                probe_call_count = 0

                async def mock_probe(ws, uid):
                    nonlocal probe_call_count
                    probe_call_count += 1
                    # After the first actual download completes, return ALL
                    if probe_call_count > 2:
                        return SyncProbeResult(
                            tier=SyncTier.ALL,
                            file_status=None,
                        )
                    return SyncProbeResult(
                        tier=SyncTier.NONE,
                        file_status=None,
                    )

                with patch(
                    "studio.app.common.core.storage.download_coordinator."
                    "SyncStateTracker"
                ) as mock_tracker:
                    mock_tracker.get_sync_probe_async = AsyncMock(
                        side_effect=mock_probe
                    )

                    with patch.object(
                        coordinator,
                        "_download_standard",
                        side_effect=mock_download_standard,
                    ):
                        # Launch 3 concurrent calls for same experiment
                        results = await asyncio.gather(
                            coordinator.ensure_synced(
                                "bucket", "ws1", "uid1", SyncTier.ALL, "c1"
                            ),
                            coordinator.ensure_synced(
                                "bucket", "ws1", "uid1", SyncTier.ALL, "c2"
                            ),
                            coordinator.ensure_synced(
                                "bucket", "ws1", "uid1", SyncTier.ALL, "c3"
                            ),
                        )

        # All should succeed
        assert all(r.success for r in results)

        # Due to in-process locking, only 1 should actually download.
        # The other 2 should be deduplicated (waiting on lock then finding
        # tier already met after re-check).
        assert download_count == 1

        DownloadCoordinator._instance = None


class TestExperimentReaderGetLocalUids:
    """ExptConfigReader.get_local_experiment_uids scans filesystem."""

    def test_returns_uids_from_glob(self):
        from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
        from studio.app.dir_path import DIRPATH

        output_dir = DIRPATH.OUTPUT_DIR.replace("\\", "/")

        with patch.object(
            ExptConfigReader,
            "get_config_yaml_wild_path",
            return_value=f"{output_dir}/ws1/*/experiment.yaml",
        ):
            with patch(
                "glob.glob",
            ) as mock_glob:
                mock_glob.return_value = [
                    f"{output_dir}/ws1/uid1/experiment.yaml",
                    f"{output_dir}/ws1/uid2/experiment.yaml",
                ]
                uids = ExptConfigReader.get_local_experiment_uids("ws1")

        assert uids == {"uid1", "uid2"}

    def test_returns_empty_set_when_no_experiments(self):
        from studio.app.common.core.experiment.experiment_reader import ExptConfigReader

        with patch.object(
            ExptConfigReader,
            "get_config_yaml_wild_path",
            return_value="/output/ws_empty/*/experiment.yaml",
        ):
            with patch(
                "glob.glob",
            ) as mock_glob:
                mock_glob.return_value = []
                uids = ExptConfigReader.get_local_experiment_uids("ws_empty")

        assert uids == set()
