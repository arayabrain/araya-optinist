"""
Unit tests for DownloadCoordinator and DownloadLimiter.

Tests ensure_synced (skip if synced, disk space check, dedup, download modes),
ensure_synced_batch, ensure_metadata_available, and DownloadLimiter.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from studio.app.common.core.storage.download_coordinator import (
    DownloadCoordinator,
    DownloadLimiter,
)
from studio.app.common.core.storage.sync_state_tracker import SyncProbeResult
from studio.app.common.core.storage.sync_tier import DownloadResult, SyncTier


@pytest.fixture(autouse=True)
def reset_coordinator_singleton():
    """Reset singleton between tests to avoid cross-test contamination."""
    DownloadCoordinator._instance = None
    yield
    DownloadCoordinator._instance = None


class TestDownloadCoordinatorSingleton:
    """Coordinator uses singleton pattern, initialized during lifespan."""

    def test_get_instance_creates_singleton(self):
        c1 = DownloadCoordinator.get_instance()
        c2 = DownloadCoordinator.get_instance()
        assert c1 is c2

    def test_initialize_creates_new_instance(self):
        c1 = DownloadCoordinator.get_instance()
        c2 = DownloadCoordinator.initialize()
        assert c1 is not c2
        assert DownloadCoordinator.get_instance() is c2


class TestDownloadLimiter:
    """DownloadLimiter provides in-process + cross-process deduplication."""

    @pytest.mark.asyncio
    async def test_get_lock_returns_asyncio_lock(self):
        limiter = DownloadLimiter()
        lock = await limiter.get_lock("ws1/uid1")
        assert isinstance(lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_same_key_returns_same_lock(self):
        limiter = DownloadLimiter()
        lock1 = await limiter.get_lock("ws1/uid1")
        lock2 = await limiter.get_lock("ws1/uid1")
        assert lock1 is lock2

    @pytest.mark.asyncio
    async def test_different_keys_return_different_locks(self):
        limiter = DownloadLimiter()
        lock1 = await limiter.get_lock("ws1/uid1")
        lock2 = await limiter.get_lock("ws1/uid2")
        assert lock1 is not lock2

    @pytest.mark.asyncio
    async def test_try_claim_and_release(self):
        """Claim can be acquired and released."""
        acquired, _ = await DownloadLimiter.try_claim(
            "ws_test", "uid_test", SyncTier.ALL, "test"
        )
        assert acquired is True

        # Release
        await DownloadLimiter.release_claim("ws_test", "uid_test")

        # Should be acquirable again
        acquired2, _ = await DownloadLimiter.try_claim(
            "ws_test", "uid_test", SyncTier.ALL, "test"
        )
        assert acquired2 is True
        await DownloadLimiter.release_claim("ws_test", "uid_test")

    @pytest.mark.asyncio
    async def test_check_claim_when_held(self):
        acquired, _ = await DownloadLimiter.try_claim(
            "ws_check", "uid_check", SyncTier.METADATA_ONLY, "test"
        )
        assert acquired is True

        is_held, data = await DownloadLimiter.check_claim("ws_check", "uid_check")
        assert is_held is True
        assert data["tier"] == int(SyncTier.METADATA_ONLY)

        await DownloadLimiter.release_claim("ws_check", "uid_check")

    @pytest.mark.asyncio
    async def test_check_claim_when_not_held(self):
        is_held, data = await DownloadLimiter.check_claim("ws_none", "uid_none")
        assert is_held is False

    @pytest.mark.asyncio
    async def test_evict_unlocked_at_capacity(self):
        """Lock eviction fires when _MAX_LOCKS is reached (Gap #8)."""
        limiter = DownloadLimiter()
        # Fill to capacity
        for i in range(DownloadLimiter._MAX_LOCKS):
            await limiter.get_lock(f"key_{i}")
        assert len(limiter._locks) == DownloadLimiter._MAX_LOCKS

        # One more should trigger eviction and succeed
        new_lock = await limiter.get_lock("overflow_key")
        assert isinstance(new_lock, asyncio.Lock)
        # After eviction, size should be well below max (all unlocked
        # keys were removed, then the new one was added)
        assert len(limiter._locks) <= DownloadLimiter._MAX_LOCKS

    @pytest.mark.asyncio
    async def test_evict_unlocked_preserves_held_locks(self):
        """Eviction keeps locks that are currently held."""
        limiter = DownloadLimiter()
        # Create and hold one lock
        held_lock = await limiter.get_lock("held_key")
        await held_lock.acquire()

        # Fill remaining capacity
        for i in range(DownloadLimiter._MAX_LOCKS - 1):
            await limiter.get_lock(f"key_{i}")

        # Trigger eviction
        await limiter.get_lock("overflow_key")

        # The held lock should survive eviction
        assert "held_key" in limiter._locks
        held_lock.release()


class TestEnsureSynced:
    """DownloadCoordinator.ensure_synced -- the main download gate."""

    def _make_probe(self, tier=SyncTier.NONE):
        return SyncProbeResult(tier=tier, file_status=None)

    @pytest.mark.asyncio
    async def test_remote_storage_unavailable(self):
        coordinator = DownloadCoordinator.initialize()

        with patch(
            "studio.app.common.core.storage.download_coordinator."
            "RemoteStorageController"
        ) as mock_rsc:
            mock_rsc.is_available.return_value = False
            result = await coordinator.ensure_synced(
                "bucket", "ws1", "uid1", SyncTier.ALL, "test"
            )

        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_skip_if_already_synced(self):
        """If current tier >= required, skip download."""
        coordinator = DownloadCoordinator.initialize()

        with patch(
            "studio.app.common.core.storage.download_coordinator."
            "RemoteStorageController"
        ) as mock_rsc:
            mock_rsc.is_available.return_value = True

            with patch.object(coordinator, "_check_disk_space", return_value=True):
                with patch(
                    "studio.app.common.core.storage.download_coordinator."
                    "SyncStateTracker"
                ) as mock_tracker:
                    mock_tracker.get_sync_probe_async = AsyncMock(
                        return_value=self._make_probe(SyncTier.ALL)
                    )

                    result = await coordinator.ensure_synced(
                        "bucket", "ws1", "uid1", SyncTier.METADATA_ONLY, "test"
                    )

        assert result.success is True
        assert result.was_skipped is True
        assert result.achieved_tier == SyncTier.ALL

    @pytest.mark.asyncio
    async def test_insufficient_disk_space(self):
        coordinator = DownloadCoordinator.initialize()

        with patch(
            "studio.app.common.core.storage.download_coordinator."
            "RemoteStorageController"
        ) as mock_rsc:
            mock_rsc.is_available.return_value = True

            with patch.object(coordinator, "_check_disk_space", return_value=False):
                result = await coordinator.ensure_synced(
                    "bucket", "ws1", "uid1", SyncTier.ALL, "test"
                )

        assert result.success is False
        assert "disk space" in result.error.lower()

    @pytest.mark.asyncio
    async def test_metadata_only_calls_download_metadata(self):
        """METADATA_ONLY tier uses _download_metadata_only."""
        coordinator = DownloadCoordinator.initialize()

        with patch(
            "studio.app.common.core.storage.download_coordinator."
            "RemoteStorageController"
        ) as mock_rsc:
            mock_rsc.is_available.return_value = True

            with patch.object(coordinator, "_check_disk_space", return_value=True):
                with patch(
                    "studio.app.common.core.storage.download_coordinator."
                    "SyncStateTracker"
                ) as mock_tracker:
                    mock_tracker.get_sync_probe_async = AsyncMock(
                        return_value=self._make_probe(SyncTier.NONE)
                    )

                    with patch(
                        "studio.app.common.core.storage.download_coordinator."
                        "AtomicClaimFile"
                    ) as mock_claim:
                        mock_claim.try_acquire_or_detect_stale.return_value = (
                            True,
                            None,
                        )
                        mock_claim.release.return_value = None

                        with patch.object(
                            coordinator,
                            "_download_metadata_only",
                            new_callable=AsyncMock,
                            return_value=SyncTier.METADATA_ONLY,
                        ) as mock_dl:
                            result = await coordinator.ensure_synced(
                                "bucket",
                                "ws1",
                                "uid1",
                                SyncTier.METADATA_ONLY,
                                "test",
                            )

        assert result.success is True
        assert result.achieved_tier == SyncTier.METADATA_ONLY
        mock_dl.assert_called_once()

    @pytest.mark.asyncio
    async def test_standard_download_for_non_metadata_tiers(self):
        """Non-METADATA tiers use _download_standard."""
        coordinator = DownloadCoordinator.initialize()

        with patch(
            "studio.app.common.core.storage.download_coordinator."
            "RemoteStorageController"
        ) as mock_rsc:
            mock_rsc.is_available.return_value = True

            with patch.object(coordinator, "_check_disk_space", return_value=True):
                with patch(
                    "studio.app.common.core.storage.download_coordinator."
                    "SyncStateTracker"
                ) as mock_tracker:
                    mock_tracker.get_sync_probe_async = AsyncMock(
                        return_value=self._make_probe(SyncTier.NONE)
                    )

                    with patch(
                        "studio.app.common.core.storage.download_coordinator."
                        "AtomicClaimFile"
                    ) as mock_claim:
                        mock_claim.try_acquire_or_detect_stale.return_value = (
                            True,
                            None,
                        )
                        mock_claim.release.return_value = None

                        with patch.object(
                            coordinator,
                            "_download_standard",
                            new_callable=AsyncMock,
                            return_value=SyncTier.ALL,
                        ) as mock_dl:
                            result = await coordinator.ensure_synced(
                                "bucket",
                                "ws1",
                                "uid1",
                                SyncTier.ALL,
                                "test",
                            )

        assert result.success is True
        mock_dl.assert_called_once_with("bucket", "ws1", "uid1", SyncTier.ALL)

    @pytest.mark.asyncio
    async def test_exclusive_lock_download(self):
        """use_exclusive_lock=True uses _download_exclusive."""
        coordinator = DownloadCoordinator.initialize()

        with patch(
            "studio.app.common.core.storage.download_coordinator."
            "RemoteStorageController"
        ) as mock_rsc:
            mock_rsc.is_available.return_value = True

            with patch.object(coordinator, "_check_disk_space", return_value=True):
                with patch(
                    "studio.app.common.core.storage.download_coordinator."
                    "SyncStateTracker"
                ) as mock_tracker:
                    mock_tracker.get_sync_probe_async = AsyncMock(
                        return_value=self._make_probe(SyncTier.NONE)
                    )

                    with patch(
                        "studio.app.common.core.storage.download_coordinator."
                        "AtomicClaimFile"
                    ) as mock_claim:
                        mock_claim.try_acquire_or_detect_stale.return_value = (
                            True,
                            None,
                        )
                        mock_claim.release.return_value = None

                        with patch.object(
                            coordinator,
                            "_download_exclusive",
                            new_callable=AsyncMock,
                            return_value=SyncTier.ALL,
                        ) as mock_dl:
                            result = await coordinator.ensure_synced(
                                "bucket",
                                "ws1",
                                "uid1",
                                SyncTier.ALL,
                                "test",
                                use_exclusive_lock=True,
                            )

        assert result.success is True
        mock_dl.assert_called_once()

    @pytest.mark.asyncio
    async def test_dedup_after_lock_acquisition(self):
        """If tier is met after acquiring lock, skip download (dedup)."""
        coordinator = DownloadCoordinator.initialize()

        with patch(
            "studio.app.common.core.storage.download_coordinator."
            "RemoteStorageController"
        ) as mock_rsc:
            mock_rsc.is_available.return_value = True

            with patch.object(coordinator, "_check_disk_space", return_value=True):
                with patch(
                    "studio.app.common.core.storage.download_coordinator."
                    "SyncStateTracker"
                ) as mock_tracker:
                    # First probe: NONE, second probe (after lock): ALL
                    mock_tracker.get_sync_probe_async = AsyncMock(
                        side_effect=[
                            self._make_probe(SyncTier.NONE),
                            self._make_probe(SyncTier.ALL),
                        ]
                    )

                    result = await coordinator.ensure_synced(
                        "bucket",
                        "ws1",
                        "uid1",
                        SyncTier.ALL,
                        "test",
                    )

        assert result.success is True
        assert result.was_deduplicated is True

    @pytest.mark.asyncio
    async def test_exception_returns_failure(self):
        """Unhandled exceptions are caught and returned as failures."""
        coordinator = DownloadCoordinator.initialize()

        with patch(
            "studio.app.common.core.storage.download_coordinator."
            "RemoteStorageController"
        ) as mock_rsc:
            mock_rsc.is_available.side_effect = RuntimeError("boom")

            result = await coordinator.ensure_synced(
                "bucket", "ws1", "uid1", SyncTier.ALL, "test"
            )

        assert result.success is False
        assert "boom" in result.error

    @pytest.mark.asyncio
    async def test_update_db_status_triggers_reconcile(self):
        """update_db_status=True calls SyncStateTracker.reconcile for ALL tier."""
        coordinator = DownloadCoordinator.initialize()

        with patch(
            "studio.app.common.core.storage.download_coordinator."
            "RemoteStorageController"
        ) as mock_rsc:
            mock_rsc.is_available.return_value = True

            with patch.object(coordinator, "_check_disk_space", return_value=True):
                with patch(
                    "studio.app.common.core.storage.download_coordinator."
                    "SyncStateTracker"
                ) as mock_tracker:
                    mock_tracker.get_sync_probe_async = AsyncMock(
                        return_value=self._make_probe(SyncTier.NONE)
                    )
                    mock_tracker.reconcile = MagicMock()

                    with patch(
                        "studio.app.common.core.storage.download_coordinator."
                        "AtomicClaimFile"
                    ) as mock_claim:
                        mock_claim.try_acquire_or_detect_stale.return_value = (
                            True,
                            None,
                        )
                        mock_claim.release.return_value = None

                        with patch.object(
                            coordinator,
                            "_download_standard",
                            new_callable=AsyncMock,
                            return_value=SyncTier.ALL,
                        ):
                            result = await coordinator.ensure_synced(
                                "bucket",
                                "ws1",
                                "uid1",
                                SyncTier.ALL,
                                "test",
                                update_db_status=True,
                            )

        assert result.success is True
        # reconcile is called via asyncio.to_thread; verify it was called
        mock_tracker.reconcile.assert_called_once_with(
            "ws1", "uid1", SyncTier.ALL, "bucket"
        )


class TestEnsureSyncedBatch:
    """ensure_synced_batch processes multiple experiments with concurrency."""

    @pytest.mark.asyncio
    async def test_batch_processes_all_experiments(self):
        coordinator = DownloadCoordinator.initialize()

        call_log = []

        async def mock_ensure_synced(
            bucket_name, workspace_id, unique_id, required_tier, caller, **kwargs
        ):
            call_log.append((workspace_id, unique_id))
            return DownloadResult(success=True, achieved_tier=required_tier)

        with patch.object(coordinator, "ensure_synced", side_effect=mock_ensure_synced):
            experiments = [("ws1", "uid1"), ("ws1", "uid2"), ("ws2", "uid3")]
            results = await coordinator.ensure_synced_batch(
                "bucket", experiments, SyncTier.METADATA_ONLY, concurrency=2
            )

        assert len(results) == 3
        assert all(r.success for r in results.values())
        assert set(call_log) == {("ws1", "uid1"), ("ws1", "uid2"), ("ws2", "uid3")}

    @pytest.mark.asyncio
    async def test_batch_respects_concurrency_limit(self):
        coordinator = DownloadCoordinator.initialize()

        max_concurrent = 0
        current_concurrent = 0

        async def mock_ensure_synced(
            bucket_name, workspace_id, unique_id, required_tier, caller, **kwargs
        ):
            nonlocal max_concurrent, current_concurrent
            current_concurrent += 1
            max_concurrent = max(max_concurrent, current_concurrent)
            await asyncio.sleep(0.01)
            current_concurrent -= 1
            return DownloadResult(success=True, achieved_tier=required_tier)

        with patch.object(coordinator, "ensure_synced", side_effect=mock_ensure_synced):
            experiments = [(f"ws{i}", f"uid{i}") for i in range(10)]
            await coordinator.ensure_synced_batch(
                "bucket", experiments, SyncTier.METADATA_ONLY, concurrency=3
            )

        assert max_concurrent <= 3


class TestEnsureMetadataAvailable:
    """ensure_metadata_available downloads metadata for DB-only experiments."""

    @pytest.mark.asyncio
    async def test_no_db_returns_early(self):
        coordinator = DownloadCoordinator.initialize()
        # db=None should return early with no side effects
        await coordinator.ensure_metadata_available("bucket", "ws1", db=None)

    @pytest.mark.asyncio
    async def test_downloads_missing_experiments(self):
        coordinator = DownloadCoordinator.initialize()

        mock_db = MagicMock()

        with patch.object(
            coordinator, "_get_experiment_uids_from_db", return_value={"uid1", "uid2"}
        ):
            with patch(
                "studio.app.common.core.experiment.experiment_reader."
                "ExptConfigReader"
            ) as mock_reader:
                mock_reader.get_local_experiment_uids.return_value = {"uid1"}

                with patch.object(
                    coordinator,
                    "ensure_synced_batch",
                    new_callable=AsyncMock,
                ) as mock_batch:
                    await coordinator.ensure_metadata_available(
                        "bucket", "ws1", db=mock_db
                    )

        mock_batch.assert_called_once()
        # Should only request download for uid2 (missing locally)
        call_args = mock_batch.call_args
        experiments = call_args.kwargs.get(
            "experiments", call_args[1].get("experiments")
        )
        uids = {uid for _, uid in experiments}
        assert uids == {"uid2"}

    @pytest.mark.asyncio
    async def test_no_missing_experiments_skips_download(self):
        coordinator = DownloadCoordinator.initialize()

        mock_db = MagicMock()

        with patch.object(
            coordinator, "_get_experiment_uids_from_db", return_value={"uid1"}
        ):
            with patch(
                "studio.app.common.core.experiment.experiment_reader."
                "ExptConfigReader"
            ) as mock_reader:
                mock_reader.get_local_experiment_uids.return_value = {"uid1"}

                with patch.object(
                    coordinator,
                    "ensure_synced_batch",
                    new_callable=AsyncMock,
                ) as mock_batch:
                    await coordinator.ensure_metadata_available(
                        "bucket", "ws1", db=mock_db
                    )

        mock_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_doesnt_propagate(self):
        coordinator = DownloadCoordinator.initialize()
        mock_db = MagicMock()

        with patch.object(
            coordinator,
            "_get_experiment_uids_from_db",
            side_effect=Exception("DB error"),
        ):
            # Should not raise
            await coordinator.ensure_metadata_available("bucket", "ws1", db=mock_db)


class TestDownloadExclusive:
    """Tests for _download_exclusive with RemoteStorageLockError (Gap #13)."""

    @pytest.mark.asyncio
    async def test_lock_error_is_reraised(self):
        """RemoteStorageLockError from exclusive download is re-raised."""
        from studio.app.common.core.storage.remote_storage_controller import (
            RemoteStorageLockError,
        )

        coordinator = DownloadCoordinator.initialize()

        with patch(
            "studio.app.common.core.storage.download_coordinator." "RemoteStorageReader"
        ) as mock_reader_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(
                side_effect=RemoteStorageLockError("ws1", "uid1")
            )
            mock_ctx.__aexit__ = AsyncMock()
            mock_reader_cls.return_value = mock_ctx

            with pytest.raises(RemoteStorageLockError):
                await coordinator._download_exclusive(
                    "bucket1", "ws1", "uid1", SyncTier.ALL
                )

    @pytest.mark.asyncio
    async def test_exclusive_download_success(self):
        """Successful exclusive download returns the requested tier."""
        coordinator = DownloadCoordinator.initialize()

        mock_controller = MagicMock()
        mock_controller.download_experiment = AsyncMock()

        with patch(
            "studio.app.common.core.storage.download_coordinator." "RemoteStorageReader"
        ) as mock_reader_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_controller)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_reader_cls.return_value = mock_ctx

            result = await coordinator._download_exclusive(
                "bucket1", "ws1", "uid1", SyncTier.ALL
            )

        assert result == SyncTier.ALL
        mock_controller.download_experiment.assert_called_once()


class TestCheckDiskSpace:
    """_check_disk_space guards against filling disk."""

    def test_sufficient_space_returns_true(self):
        mock_usage = MagicMock()
        mock_usage.free = 10 * 1024 * 1024 * 1024  # 10 GB

        with patch("shutil.disk_usage", return_value=mock_usage):
            assert DownloadCoordinator._check_disk_space() is True

    def test_insufficient_space_returns_false(self):
        mock_usage = MagicMock()
        mock_usage.free = 500 * 1024 * 1024  # 500 MB (below 1 GB threshold)

        with patch("shutil.disk_usage", return_value=mock_usage):
            assert DownloadCoordinator._check_disk_space() is False

    def test_os_error_returns_true(self):
        """If we can't check, proceed anyway."""
        with patch("shutil.disk_usage", side_effect=OSError("not mounted")):
            assert DownloadCoordinator._check_disk_space() is True


class TestGetExperimentUidsFromDb:
    """_get_experiment_uids_from_db extracts UIDs from DB."""

    def test_returns_uid_set(self):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [
            ("uid1",),
            ("uid2",),
        ]

        with patch(
            "studio.app.common.core.experiment.experiment_record_services."
            "ExperimentRecordService"
        ) as mock_svc:
            mock_svc.is_available.return_value = True
            result = DownloadCoordinator._get_experiment_uids_from_db(mock_db, "ws1")

        assert result == {"uid1", "uid2"}

    def test_service_unavailable_returns_empty(self):
        mock_db = MagicMock()

        with patch(
            "studio.app.common.core.experiment.experiment_record_services."
            "ExperimentRecordService"
        ) as mock_svc:
            mock_svc.is_available.return_value = False
            result = DownloadCoordinator._get_experiment_uids_from_db(mock_db, "ws1")

        assert result == set()

    def test_exception_returns_empty(self):
        mock_db = MagicMock()
        mock_db.query.side_effect = Exception("DB error")

        result = DownloadCoordinator._get_experiment_uids_from_db(mock_db, "ws1")
        assert result == set()
