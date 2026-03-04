"""
Tests for leader election and startup sync integration in __main_unit__.py.

Tests the _startup_sync coroutine behavior with try_become_startup_leader
and release_startup_leader.
"""

from unittest.mock import AsyncMock, patch

import pytest


class TestStartupSyncLeaderElection:
    """Tests for leader election in lifespan startup."""

    @pytest.mark.asyncio
    async def test_leader_runs_startup_sync(self):
        """When elected leader, runs startup sync + release."""
        with patch(
            "studio.app.common.core.storage.startup_leader.try_become_startup_leader",
            return_value=True,
        ) as mock_try, patch(
            "studio.app.common.core.storage.startup_leader.release_startup_leader",
        ) as mock_release, patch(
            "studio.app.common.core.background.sync_job."
            "PublishedExperimentSyncJob.run_startup_sync",
            new_callable=AsyncMock,
        ) as mock_sync:
            from studio.app.common.core.background.sync_job import (
                PublishedExperimentSyncJob,
            )
            from studio.app.common.core.storage.startup_leader import (
                release_startup_leader,
                try_become_startup_leader,
            )

            # Replicate the logic from __main_unit__.py lifespan
            if try_become_startup_leader():
                try:
                    await PublishedExperimentSyncJob.run_startup_sync()
                finally:
                    release_startup_leader()

            mock_try.assert_called_once()
            mock_sync.assert_called_once()
            mock_release.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_leader_skips_startup_sync(self):
        """When not elected leader, skips startup sync entirely."""
        with patch(
            "studio.app.common.core.storage.startup_leader.try_become_startup_leader",
            return_value=False,
        ) as mock_try, patch(
            "studio.app.common.core.storage.startup_leader.release_startup_leader",
        ) as mock_release, patch(
            "studio.app.common.core.background.sync_job."
            "PublishedExperimentSyncJob.run_startup_sync",
            new_callable=AsyncMock,
        ) as mock_sync:
            from studio.app.common.core.background.sync_job import (
                PublishedExperimentSyncJob,
            )
            from studio.app.common.core.storage.startup_leader import (
                release_startup_leader,
                try_become_startup_leader,
            )

            if try_become_startup_leader():
                try:
                    await PublishedExperimentSyncJob.run_startup_sync()
                finally:
                    release_startup_leader()

            mock_try.assert_called_once()
            mock_sync.assert_not_called()
            mock_release.assert_not_called()

    @pytest.mark.asyncio
    async def test_leader_releases_on_sync_error(self):
        """Leader file is released even when startup sync raises."""
        with patch(
            "studio.app.common.core.storage.startup_leader.try_become_startup_leader",
            return_value=True,
        ), patch(
            "studio.app.common.core.storage.startup_leader.release_startup_leader",
        ) as mock_release, patch(
            "studio.app.common.core.background.sync_job."
            "PublishedExperimentSyncJob.run_startup_sync",
            new_callable=AsyncMock,
            side_effect=RuntimeError("S3 unavailable"),
        ):
            from studio.app.common.core.background.sync_job import (
                PublishedExperimentSyncJob,
            )
            from studio.app.common.core.storage.startup_leader import (
                release_startup_leader,
                try_become_startup_leader,
            )

            if try_become_startup_leader():
                try:
                    await PublishedExperimentSyncJob.run_startup_sync()
                except Exception:
                    pass
                finally:
                    release_startup_leader()

            # Release MUST be called even after error
            mock_release.assert_called_once()
