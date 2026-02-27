"""
Unit tests for published experiment sync job.

Tests S3 validation, proactive download trigger, and startup sync.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from studio.app.common.core.background.sync_job import PublishedExperimentSyncJob


class TestStartupSync:
    """Test one-time startup sync for API containers"""

    @pytest.mark.asyncio
    async def test_downloads_missing_experiments(self):
        """Test startup sync downloads experiments missing locally via coordinator"""
        published = [
            ("ws1", "uid1", 1, "bucket1"),
            ("ws2", "uid2", 2, "bucket1"),
        ]

        mock_coordinator = MagicMock()
        mock_coordinator.ensure_synced_batch = AsyncMock(return_value=[])

        with patch.object(
            PublishedExperimentSyncJob,
            "_get_all_published_experiments",
            return_value=published,
        ):
            with patch("os.path.exists", return_value=False):
                with patch(
                    "studio.app.common.core.storage.download_coordinator."
                    "DownloadCoordinator.get_instance",
                    return_value=mock_coordinator,
                ):
                    await PublishedExperimentSyncJob.run_startup_sync()

        # 2 phases (THUMBNAILS_ONLY + ESSENTIAL_ONLY), 1 bucket each = 2 calls
        assert mock_coordinator.ensure_synced_batch.call_count == 2

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


class TestProactiveDownloadTrigger:
    """Tests for _trigger_proactive_download ALB call"""

    @pytest.mark.asyncio
    async def test_trigger_success(self):
        """Successful POST returns True"""
        env = {
            "ALB_DNS_NAME": "test-alb.example.com",
            "INTERNAL_API_SECRET": "secret-123",
        }
        with patch.dict(os.environ, env):
            with patch("requests.post") as mock_post:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_post.return_value = mock_resp

                result = await PublishedExperimentSyncJob._trigger_proactive_download(
                    "ws1", "uid1", "bucket1"
                )

                assert result is True
                mock_post.assert_called_once()
                call_url = mock_post.call_args[0][0]
                assert "sync-experiment/ws1/uid1" in call_url
                params = mock_post.call_args[1]["params"]
                assert params["bucket_name"] == "bucket1"

    @pytest.mark.asyncio
    async def test_trigger_passes_has_thumbnails(self):
        """has_thumbnails is passed to ALB request"""
        env = {
            "ALB_DNS_NAME": "test-alb.example.com",
            "INTERNAL_API_SECRET": "secret-123",
        }
        with patch.dict(os.environ, env):
            with patch("requests.post") as mock_post:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_post.return_value = mock_resp

                await PublishedExperimentSyncJob._trigger_proactive_download(
                    "ws1",
                    "uid1",
                    "bucket1",
                    has_thumbnails=False,
                )

                params = mock_post.call_args[1]["params"]
                assert params["has_thumbnails"] == "false"

    @pytest.mark.asyncio
    async def test_trigger_missing_config(self):
        """Missing ALB_DNS_NAME returns False"""
        env = {"ALB_DNS_NAME": "", "INTERNAL_API_SECRET": ""}
        with patch.dict(os.environ, env, clear=False):
            result = await PublishedExperimentSyncJob._trigger_proactive_download(
                "ws1", "uid1", "bucket1"
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_trigger_connection_error(self):
        """ConnectionError returns False"""
        import requests

        env = {
            "ALB_DNS_NAME": "test-alb.example.com",
            "INTERNAL_API_SECRET": "secret",
        }
        with patch.dict(os.environ, env):
            with patch("requests.post") as mock_post:
                mock_post.side_effect = requests.exceptions.ConnectionError(
                    "Network error"
                )

                result = await PublishedExperimentSyncJob._trigger_proactive_download(
                    "ws1", "uid1", "bucket1"
                )

                assert result is False

    @pytest.mark.asyncio
    async def test_trigger_api_error(self):
        """500 response returns False"""
        env = {
            "ALB_DNS_NAME": "test-alb.example.com",
            "INTERNAL_API_SECRET": "secret",
        }
        with patch.dict(os.environ, env):
            with patch("requests.post") as mock_post:
                mock_resp = MagicMock()
                mock_resp.status_code = 500
                mock_post.return_value = mock_resp

                result = await PublishedExperimentSyncJob._trigger_proactive_download(
                    "ws1", "uid1", "bucket1"
                )

                assert result is False

    @pytest.mark.asyncio
    async def test_trigger_timeout(self):
        """Timeout returns False"""
        import requests

        env = {
            "ALB_DNS_NAME": "test-alb.example.com",
            "INTERNAL_API_SECRET": "secret",
        }
        with patch.dict(os.environ, env):
            with patch("requests.post") as mock_post:
                mock_post.side_effect = requests.exceptions.Timeout("Request timed out")

                result = await PublishedExperimentSyncJob._trigger_proactive_download(
                    "ws1", "uid1", "bucket1"
                )

                assert result is False

    @pytest.mark.asyncio
    async def test_trigger_missing_only_alb(self):
        """ALB set but no secret returns False"""
        env = {
            "ALB_DNS_NAME": "test-alb.example.com",
            "INTERNAL_API_SECRET": "",
        }
        with patch.dict(os.environ, env, clear=False):
            result = await PublishedExperimentSyncJob._trigger_proactive_download(
                "ws1", "uid1", "bucket1"
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_trigger_missing_only_secret(self):
        """Secret set but no ALB returns False"""
        env = {
            "ALB_DNS_NAME": "",
            "INTERNAL_API_SECRET": "secret",
        }
        with patch.dict(os.environ, env, clear=False):
            result = await PublishedExperimentSyncJob._trigger_proactive_download(
                "ws1", "uid1", "bucket1"
            )

            assert result is False


class TestValidateExperiment:
    """Tests for _validate_experiment with proactive trigger"""

    @pytest.mark.asyncio
    async def test_validate_success_triggers_download(self):
        """Successful validation triggers proactive download"""
        mock_s3 = MagicMock()
        mock_s3.validate_experiment_in_s3 = AsyncMock(
            return_value={
                "valid": True,
                "has_thumbnails": True,
            }
        )

        with patch.object(
            PublishedExperimentSyncJob,
            "_mark_sync_complete",
        ), patch.object(
            PublishedExperimentSyncJob,
            "_clear_retry_count",
        ), patch.object(
            PublishedExperimentSyncJob,
            "_trigger_proactive_download",
            new_callable=AsyncMock,
        ) as mock_trigger:
            result = await PublishedExperimentSyncJob._validate_experiment(
                mock_s3, "ws1", "uid1", 1, "bucket1"
            )

            assert result is True
            mock_trigger.assert_called_once_with(
                "ws1",
                "uid1",
                "bucket1",
                has_thumbnails=True,
            )

    @pytest.mark.asyncio
    async def test_validate_passes_has_thumbnails_false(self):
        """has_thumbnails=False is forwarded to trigger"""
        mock_s3 = MagicMock()
        mock_s3.validate_experiment_in_s3 = AsyncMock(
            return_value={
                "valid": True,
                "has_thumbnails": False,
            }
        )

        with patch.object(
            PublishedExperimentSyncJob,
            "_mark_sync_complete",
        ), patch.object(
            PublishedExperimentSyncJob,
            "_clear_retry_count",
        ), patch.object(
            PublishedExperimentSyncJob,
            "_trigger_proactive_download",
            new_callable=AsyncMock,
        ) as mock_trigger:
            result = await PublishedExperimentSyncJob._validate_experiment(
                mock_s3, "ws1", "uid1", 1, "bucket1"
            )

            assert result is True
            mock_trigger.assert_called_once_with(
                "ws1",
                "uid1",
                "bucket1",
                has_thumbnails=False,
            )

    @pytest.mark.asyncio
    async def test_validate_failure_no_trigger(self):
        """Failed validation does not trigger download"""
        mock_s3 = MagicMock()
        mock_s3.validate_experiment_in_s3 = AsyncMock(
            return_value={
                "valid": False,
                "error": "Missing files",
            }
        )

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch.object(
                PublishedExperimentSyncJob,
                "_mark_sync_error",
            ),
            patch.object(
                PublishedExperimentSyncJob,
                "_increment_retry_count",
            ),
            patch.object(
                PublishedExperimentSyncJob,
                "_check_persistent_failure",
            ),
            patch.object(
                PublishedExperimentSyncJob,
                "_trigger_proactive_download",
                new_callable=AsyncMock,
            ) as mock_trigger,
        ):
            result = await PublishedExperimentSyncJob._validate_experiment(
                mock_s3, "ws1", "uid1", 1, "bucket1"
            )

            assert result is False
            mock_trigger.assert_not_called()

    @pytest.mark.asyncio
    async def test_validate_retry_then_succeed(self):
        """Fail twice then succeed on third attempt"""
        mock_s3 = MagicMock()
        mock_s3.validate_experiment_in_s3 = AsyncMock(
            side_effect=[
                {"valid": False, "error": "Transient error"},
                {"valid": False, "error": "Transient error"},
                {"valid": True, "has_thumbnails": True},
            ]
        )

        with (
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch.object(
                PublishedExperimentSyncJob,
                "_mark_sync_complete",
            ) as mock_complete,
            patch.object(
                PublishedExperimentSyncJob,
                "_clear_retry_count",
            ),
            patch.object(
                PublishedExperimentSyncJob,
                "_trigger_proactive_download",
                new_callable=AsyncMock,
            ),
        ):
            result = await PublishedExperimentSyncJob._validate_experiment(
                mock_s3, "ws1", "uid1", 1, "bucket1"
            )

            assert result is True
            assert mock_s3.validate_experiment_in_s3.call_count == 3
            # 2 retries: 2^0=1, 2^1=2
            assert mock_sleep.call_count == 2
            mock_sleep.assert_any_call(1)
            mock_sleep.assert_any_call(2)
            mock_complete.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_validate_generic_exception_retries(self):
        """Generic exception (not SyncRetryError) also retries"""
        mock_s3 = MagicMock()
        mock_s3.validate_experiment_in_s3 = AsyncMock(
            side_effect=[
                RuntimeError("S3 timeout"),
                {"valid": True, "has_thumbnails": False},
            ]
        )

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch.object(
                PublishedExperimentSyncJob,
                "_mark_sync_complete",
            ),
            patch.object(
                PublishedExperimentSyncJob,
                "_clear_retry_count",
            ),
            patch.object(
                PublishedExperimentSyncJob,
                "_trigger_proactive_download",
                new_callable=AsyncMock,
            ),
        ):
            result = await PublishedExperimentSyncJob._validate_experiment(
                mock_s3, "ws1", "uid1", 1, "bucket1"
            )

            assert result is True
            assert mock_s3.validate_experiment_in_s3.call_count == 2

    @pytest.mark.asyncio
    async def test_validate_all_retries_exhausted(self):
        """All 3 retries fail -> marks error + persistent failure"""
        mock_s3 = MagicMock()
        mock_s3.validate_experiment_in_s3 = AsyncMock(
            return_value={
                "valid": False,
                "error": "Missing",
            }
        )

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch.object(
                PublishedExperimentSyncJob,
                "_mark_sync_error",
            ) as mock_error,
            patch.object(
                PublishedExperimentSyncJob,
                "_increment_retry_count",
            ) as mock_inc,
            patch.object(
                PublishedExperimentSyncJob,
                "_check_persistent_failure",
            ) as mock_persist,
        ):
            result = await PublishedExperimentSyncJob._validate_experiment(
                mock_s3, "ws1", "uid1", 1, "bucket1"
            )

            assert result is False
            assert mock_s3.validate_experiment_in_s3.call_count == 3
            mock_error.assert_called_once_with(1)
            mock_inc.assert_called_once_with(1)
            mock_persist.assert_called_once_with(1, "ws1", "uid1")

    @pytest.mark.asyncio
    async def test_validate_outer_exception(self):
        """Exception outside retry loop -> marks error"""
        with (
            patch.object(
                PublishedExperimentSyncJob,
                "_mark_sync_error",
            ) as mock_error,
            patch.object(
                PublishedExperimentSyncJob,
                "_increment_retry_count",
            ) as mock_inc,
        ):
            bad_s3 = MagicMock()
            bad_s3.validate_experiment_in_s3 = AsyncMock(
                side_effect=RuntimeError("broken")
            )
            with patch(
                "builtins.range",
                side_effect=RuntimeError("outer"),
            ):
                result = await PublishedExperimentSyncJob._validate_experiment(
                    bad_s3, "ws1", "uid1", 1, "bucket1"
                )

            assert result is False
            mock_error.assert_called_once_with(1)
            mock_inc.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_validate_default_empty_bucket_name(self):
        """Default bucket_name='' is passed to trigger"""
        mock_s3 = MagicMock()
        mock_s3.validate_experiment_in_s3 = AsyncMock(
            return_value={
                "valid": True,
                "has_thumbnails": True,
            }
        )

        with patch.object(
            PublishedExperimentSyncJob,
            "_mark_sync_complete",
        ), patch.object(
            PublishedExperimentSyncJob,
            "_clear_retry_count",
        ), patch.object(
            PublishedExperimentSyncJob,
            "_trigger_proactive_download",
            new_callable=AsyncMock,
        ) as mock_trigger:
            result = await PublishedExperimentSyncJob._validate_experiment(
                mock_s3, "ws1", "uid1", 1
            )

            assert result is True
            mock_trigger.assert_called_once_with(
                "ws1",
                "uid1",
                "",
                has_thumbnails=True,
            )


class TestRetryCount:
    """Tests for module-level retry count tracking"""

    def setup_method(self):
        """Reset retry counts before each test."""
        from studio.app.common.core.background import sync_job

        sync_job._retry_counts.clear()

    def test_increment_retry_count(self):
        """Incrementing tracks count per experiment"""
        cls = PublishedExperimentSyncJob
        cls._increment_retry_count(42)
        cls._increment_retry_count(42)
        cls._increment_retry_count(99)

        from studio.app.common.core.background import sync_job

        assert sync_job._retry_counts[42] == 2
        assert sync_job._retry_counts[99] == 1

    def test_clear_retry_count(self):
        """Clearing removes entry entirely"""
        cls = PublishedExperimentSyncJob
        cls._increment_retry_count(42)
        cls._clear_retry_count(42)

        from studio.app.common.core.background import sync_job

        assert 42 not in sync_job._retry_counts

    def test_persistent_failure_below_threshold(self):
        """Below MAX_PERSISTENT_RETRIES: no metric"""
        from studio.app.common.core.background import sync_job

        cls = PublishedExperimentSyncJob
        for _ in range(sync_job.MAX_PERSISTENT_RETRIES - 1):
            cls._increment_retry_count(42)

        with patch("boto3.client") as mock_boto:
            cls._check_persistent_failure(42, "ws1", "uid1")
            mock_boto.assert_not_called()

    def test_persistent_failure_at_threshold(self):
        """At MAX_PERSISTENT_RETRIES: publishes metric"""
        from studio.app.common.core.background import sync_job

        cls = PublishedExperimentSyncJob
        for _ in range(sync_job.MAX_PERSISTENT_RETRIES):
            cls._increment_retry_count(42)

        with patch("boto3.client") as mock_boto:
            mock_cw = MagicMock()
            mock_boto.return_value = mock_cw
            cls._check_persistent_failure(42, "ws1", "uid1")
            mock_cw.put_metric_data.assert_called_once()
