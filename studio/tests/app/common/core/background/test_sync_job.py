"""
Unit tests for published experiment sync job.

Tests S3 download with exponential backoff and error handling.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from studio.app.common.core.background.sync_job import PublishedExperimentSyncJob


class TestSyncExperiment:
    """Test experiment sync with retry logic"""

    @pytest.mark.asyncio
    async def test_sync_experiment_success_first_attempt(self):
        """Test successful sync on first attempt"""
        mock_s3_controller = MagicMock()
        mock_s3_controller.download_experiment = AsyncMock(return_value=True)

        with patch("studio.app.common.core.background.sync_job.session_scope"):
            with patch("os.path.exists", return_value=False):
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

        with patch("studio.app.common.core.background.sync_job.session_scope"):
            with patch("os.path.exists", return_value=False):
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
