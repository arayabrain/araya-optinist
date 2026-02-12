"""
Unit tests for published experiment sync job.

Tests S3 download with exponential backoff and error handling.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from studio.app.common.core.background.sync_job import PublishedExperimentSyncJob
from studio.app.dir_path import DIRPATH


def make_path_exists_mock(local_path_exists=False, required_files_exist=True):
    """
    Create a mock for os.path.exists that handles different paths.

    Args:
        local_path_exists: Whether the local experiment directory exists
        required_files_exist: Whether required files exist after download
    """
    call_count = [0]

    def path_exists(path):
        call_count[0] += 1
        # First call checks if local_path exists
        if call_count[0] == 1:
            return local_path_exists

        # If local_path exists and we're checking required files
        if local_path_exists:
            return required_files_exist

        # After download, check if required files exist
        if DIRPATH.EXPERIMENT_YML in path or DIRPATH.WORKFLOW_YML in path:
            return required_files_exist

        return False

    return path_exists


class TestSyncExperiment:
    """Test experiment sync with retry logic"""

    @pytest.mark.asyncio
    async def test_sync_experiment_success_first_attempt(self):
        """Test successful sync on first attempt"""
        mock_s3_controller = MagicMock()
        mock_s3_controller.download_experiment = AsyncMock(return_value=True)

        with patch("studio.app.common.core.background.sync_job.session_scope"):
            with patch(
                "os.path.exists",
                side_effect=make_path_exists_mock(
                    local_path_exists=False, required_files_exist=True
                ),
            ):
                result = await PublishedExperimentSyncJob._sync_experiment(
                    mock_s3_controller, "workspace1", "exp123", 1
                )

        assert result is True
        assert mock_s3_controller.download_experiment.call_count == 1

    @pytest.mark.asyncio
    async def test_sync_experiment_retry_on_failure(self):
        """Test exponential backoff retry on failure"""
        mock_s3_controller = MagicMock()
        mock_s3_controller.download_experiment = AsyncMock(
            side_effect=[False, False, True]
        )

        # Track calls to determine when files should "exist"
        download_attempts = [0]

        def path_exists_after_success(path):
            # Local path doesn't exist initially
            if download_attempts[0] == 0:
                return False
            # After successful download, files exist
            if DIRPATH.EXPERIMENT_YML in path or DIRPATH.WORKFLOW_YML in path:
                return download_attempts[0] >= 3  # Only after 3rd attempt (success)
            return False

        original_download = mock_s3_controller.download_experiment

        async def track_download(*args, **kwargs):
            download_attempts[0] += 1
            return await original_download(*args, **kwargs)

        mock_s3_controller.download_experiment = AsyncMock(side_effect=track_download)
        mock_s3_controller.download_experiment.side_effect = None
        mock_s3_controller.download_experiment = AsyncMock(
            side_effect=[False, False, True]
        )

        with patch("studio.app.common.core.background.sync_job.session_scope"):
            with patch(
                "os.path.exists",
                side_effect=make_path_exists_mock(
                    local_path_exists=False, required_files_exist=True
                ),
            ):
                with patch("asyncio.sleep") as mock_sleep:
                    result = await PublishedExperimentSyncJob._sync_experiment(
                        mock_s3_controller, "workspace1", "exp123", 1
                    )

        assert result is True
        assert mock_s3_controller.download_experiment.call_count == 3
        assert mock_sleep.call_count == 2

    @pytest.mark.asyncio
    async def test_sync_experiment_all_retries_fail(self):
        """Test failure after all retries exhausted"""
        mock_s3_controller = MagicMock()
        mock_s3_controller.download_experiment = AsyncMock(return_value=False)

        with patch("studio.app.common.core.background.sync_job.session_scope"):
            with patch("os.path.exists", return_value=False):
                with patch("asyncio.sleep"):
                    result = await PublishedExperimentSyncJob._sync_experiment(
                        mock_s3_controller, "workspace1", "exp123", 1
                    )

        assert result is False
        assert mock_s3_controller.download_experiment.call_count == 3

    @pytest.mark.asyncio
    async def test_sync_experiment_missing_required_files_in_s3(self):
        """Test failure when required files are missing from S3 (corrupted data)"""
        mock_s3_controller = MagicMock()
        # Download returns True (S3 has some files) but required files are missing
        mock_s3_controller.download_experiment = AsyncMock(return_value=True)

        with patch("studio.app.common.core.background.sync_job.session_scope"):
            # Required files don't exist after download
            with patch(
                "os.path.exists",
                side_effect=make_path_exists_mock(
                    local_path_exists=False, required_files_exist=False
                ),
            ):
                with patch("asyncio.sleep"):
                    result = await PublishedExperimentSyncJob._sync_experiment(
                        mock_s3_controller, "workspace1", "exp123", 1
                    )

        # Should fail because required files are missing
        assert result is False
        # Should retry 3 times before giving up
        assert mock_s3_controller.download_experiment.call_count == 3

    @pytest.mark.asyncio
    async def test_sync_experiment_skips_when_local_files_exist(self):
        """Test sync skips download when local files already exist"""
        mock_s3_controller = MagicMock()
        mock_s3_controller.download_experiment = AsyncMock(return_value=True)

        with patch("studio.app.common.core.background.sync_job.session_scope"):
            # Local path exists and has required files
            with patch(
                "os.path.exists",
                side_effect=make_path_exists_mock(
                    local_path_exists=True, required_files_exist=True
                ),
            ):
                result = await PublishedExperimentSyncJob._sync_experiment(
                    mock_s3_controller, "workspace1", "exp123", 1
                )

        assert result is True
        # Should NOT call download because local files exist
        assert mock_s3_controller.download_experiment.call_count == 0


class TestStartupSync:
    """Test one-time startup sync for API containers"""

    @pytest.mark.asyncio
    async def test_downloads_missing_experiments(self):
        """Test startup sync downloads experiments missing locally"""
        published = [
            ("ws1", "uid1", 1, "bucket1"),
            ("ws2", "uid2", 2, "bucket1"),
        ]

        mock_s3 = MagicMock()
        mock_s3.download_experiment = AsyncMock(return_value=True)

        with patch.object(
            PublishedExperimentSyncJob,
            "_get_all_published_experiments",
            return_value=published,
        ):
            with patch("os.path.exists", return_value=False):
                with patch(
                    "studio.app.common.core.background" ".sync_job.S3StorageController",
                    return_value=mock_s3,
                ):
                    await PublishedExperimentSyncJob.run_startup_sync()

        # 2 experiments x 2 phases = 4 download calls
        assert mock_s3.download_experiment.call_count == 4

    @pytest.mark.asyncio
    async def test_skips_locally_present_experiments(self):
        """Test startup sync skips experiments already on disk"""
        published = [
            ("ws1", "uid1", 1, "bucket1"),
        ]

        mock_s3 = MagicMock()
        mock_s3.download_experiment = AsyncMock(return_value=True)

        with patch.object(
            PublishedExperimentSyncJob,
            "_get_all_published_experiments",
            return_value=published,
        ):
            # Both yaml files exist locally
            with patch("os.path.exists", return_value=True):
                with patch(
                    "studio.app.common.core.background" ".sync_job.S3StorageController",
                    return_value=mock_s3,
                ):
                    await PublishedExperimentSyncJob.run_startup_sync()

        assert mock_s3.download_experiment.call_count == 0

    @pytest.mark.asyncio
    async def test_handles_empty_published_list(self):
        """Test startup sync handles no published experiments"""
        with patch.object(
            PublishedExperimentSyncJob,
            "_get_all_published_experiments",
            return_value=[],
        ):
            # Should not raise
            await PublishedExperimentSyncJob.run_startup_sync()
