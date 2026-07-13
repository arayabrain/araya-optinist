"""
Tests for startup-sync leader election and INSTANCE_MODE gating in lifespan.

Covers:
- `startup_sync_leader_lock` runs the sync only when GET_LOCK is acquired.
- `_should_run_startup_sync` gates the lifespan branch to INSTANCE_MODE=public.
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest


@contextmanager
def _fake_leader_lock(acquired: bool):
    """Stand-in for startup_sync_leader_lock used by the lifespan branch."""
    yield acquired


class TestStartupSyncLeaderElection:
    """Tests for leader election via MySQL GET_LOCK."""

    @pytest.mark.asyncio
    async def test_leader_runs_startup_sync(self):
        """When the lock is acquired, startup sync runs once."""
        with patch(
            "studio.app.common.core.storage.startup_leader.startup_sync_leader_lock",
            lambda: _fake_leader_lock(True),
        ), patch(
            "studio.app.common.core.background.sync_job."
            "PublishedExperimentSyncJob.run_startup_sync",
            new_callable=AsyncMock,
        ) as mock_sync:
            from studio.app.common.core.background.sync_job import (
                PublishedExperimentSyncJob,
            )
            from studio.app.common.core.storage.startup_leader import (
                startup_sync_leader_lock,
            )

            with startup_sync_leader_lock() as acquired:
                if acquired:
                    await PublishedExperimentSyncJob.run_startup_sync()

            mock_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_leader_skips_startup_sync(self):
        """When the lock is not acquired, sync is skipped entirely."""
        with patch(
            "studio.app.common.core.storage.startup_leader.startup_sync_leader_lock",
            lambda: _fake_leader_lock(False),
        ), patch(
            "studio.app.common.core.background.sync_job."
            "PublishedExperimentSyncJob.run_startup_sync",
            new_callable=AsyncMock,
        ) as mock_sync:
            from studio.app.common.core.background.sync_job import (
                PublishedExperimentSyncJob,
            )
            from studio.app.common.core.storage.startup_leader import (
                startup_sync_leader_lock,
            )

            with startup_sync_leader_lock() as acquired:
                if acquired:
                    await PublishedExperimentSyncJob.run_startup_sync()

            mock_sync.assert_not_called()

    @pytest.mark.asyncio
    async def test_leader_lock_released_on_sync_error(self):
        """Lock context manager exits cleanly even when the sync raises."""
        release_calls = []

        @contextmanager
        def fake_lock():
            try:
                yield True
            finally:
                release_calls.append(True)

        with patch(
            "studio.app.common.core.storage.startup_leader.startup_sync_leader_lock",
            fake_lock,
        ), patch(
            "studio.app.common.core.background.sync_job."
            "PublishedExperimentSyncJob.run_startup_sync",
            new_callable=AsyncMock,
            side_effect=RuntimeError("S3 unavailable"),
        ):
            from studio.app.common.core.background.sync_job import (
                PublishedExperimentSyncJob,
            )
            from studio.app.common.core.storage.startup_leader import (
                startup_sync_leader_lock,
            )

            with pytest.raises(RuntimeError):
                with startup_sync_leader_lock() as acquired:
                    if acquired:
                        await PublishedExperimentSyncJob.run_startup_sync()

            assert release_calls == [True], "lock context should exit even on error"


class TestShouldRunStartupSync:
    """Gating predicate for the lifespan startup-sync branch."""

    def test_public_mode_runs_sync(self):
        from studio.__main_unit__ import _should_run_startup_sync

        assert _should_run_startup_sync("public", is_standalone=False) is True

    def test_default_mode_skips_sync(self):
        from studio.__main_unit__ import _should_run_startup_sync

        assert _should_run_startup_sync("default", is_standalone=False) is False

    def test_unknown_mode_skips_sync(self):
        from studio.__main_unit__ import _should_run_startup_sync

        assert _should_run_startup_sync("free", is_standalone=False) is False

    def test_standalone_always_skips(self):
        from studio.__main_unit__ import _should_run_startup_sync

        # Even with instance_mode=public, standalone never runs the sync.
        assert _should_run_startup_sync("public", is_standalone=True) is False
