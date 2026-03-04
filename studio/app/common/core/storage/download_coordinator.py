import asyncio
import shutil
import threading
import time
from typing import Dict, List, Optional, Tuple

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    RemoteStorageLockError,
    RemoteStorageReader,
)
from studio.app.common.core.storage.sync_state_tracker import SyncStateTracker
from studio.app.common.core.storage.sync_tier import DownloadResult, SyncTier

logger = AppLogger.get_logger()

# Minimum free disk space (bytes) before refusing downloads (EC-5)
_MIN_FREE_DISK_BYTES = 1 * 1024 * 1024 * 1024  # 1 GB


class DownloadLimiter:
    """In-process deduplication for download operations.

    Uses asyncio.Lock per experiment key to prevent concurrent downloads
    of the same experiment within a single worker process.
    Cross-process dedup is handled by the startup leader election system.
    """

    _MAX_LOCKS = 1000

    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def get_lock(self, key: str) -> asyncio.Lock:
        """Get or create an asyncio.Lock for an experiment key."""
        async with self._locks_guard:
            if key not in self._locks:
                if len(self._locks) >= self._MAX_LOCKS:
                    self._evict_unlocked()
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    def _evict_unlocked(self) -> None:
        """Remove entries whose locks are not currently held."""
        to_remove = [k for k, v in self._locks.items() if not v.locked()]
        for k in to_remove:
            del self._locks[k]


class DownloadCoordinator:
    """Single gate for all download operations.

    Every one of the 9 download paths calls this instead of directly
    constructing RemoteStorageSimpleReader or RemoteStorageReader.

    Must be initialized during FastAPI lifespan (post-fork), not lazily (EC-21).
    Each uvicorn worker gets its own singleton instance.
    """

    _instance: Optional["DownloadCoordinator"] = None
    _init_lock = threading.Lock()

    def __init__(self):
        self._limiter = DownloadLimiter()

    @classmethod
    def get_instance(cls) -> "DownloadCoordinator":
        """Get the singleton coordinator instance.

        Thread-safe lazy initialization as fallback (EC-21).
        Prefer explicit initialization via initialize() in lifespan.
        """
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
                    logger.info("coordinator.initialized")
        return cls._instance

    @classmethod
    def initialize(cls) -> "DownloadCoordinator":
        """Explicitly initialize the coordinator during FastAPI lifespan."""
        with cls._init_lock:
            cls._instance = cls()
        logger.info("coordinator.initialized_explicit")
        return cls._instance

    async def ensure_synced(
        self,
        bucket_name: str,
        workspace_id: str,
        unique_id: str,
        required_tier: SyncTier,
        caller: str = "",
        use_exclusive_lock: bool = False,
        update_db_status: bool = False,
    ) -> DownloadResult:
        """Ensure an experiment is synced to at least the required tier.

        Never raises -- always returns a DownloadResult.

        1. Check current sync state (SyncStateTracker) -- skip if already done
        2. Acquire in-process dedup lock (DownloadLimiter)
        3. For METADATA_ONLY: call download_experiment_meta()
           For higher tiers: call download_experiment(sync_mode=tier.to_sync_mode())
           For exclusive paths: use RemoteStorageReader context manager
        4. Update sync state on completion (SyncStateTracker)
        5. Reconcile DB + file status when appropriate
        """
        start_time = time.monotonic()

        try:
            if not RemoteStorageController.is_available():
                return DownloadResult(
                    success=False,
                    achieved_tier=SyncTier.NONE,
                    error="Remote storage not available",
                )

            # Check disk space (EC-5)
            if not self._check_disk_space():
                logger.error(
                    f"coordinator.disk_space_low "
                    f"caller={caller} experiment={workspace_id}/{unique_id}"
                )
                return DownloadResult(
                    success=False,
                    achieved_tier=SyncTier.NONE,
                    error="Insufficient disk space",
                )

            # Step 1: Check current tier from filesystem
            probe = await SyncStateTracker.get_sync_probe_async(workspace_id, unique_id)
            current_tier = probe.tier

            if current_tier >= required_tier:
                logger.debug(
                    f"coordinator.download_skipped "
                    f"reason=already_synced "
                    f"experiment={workspace_id}/{unique_id} "
                    f"current_tier={current_tier.name} "
                    f"required_tier={required_tier.name} "
                    f"caller={caller}"
                )
                return DownloadResult(
                    success=True,
                    achieved_tier=current_tier,
                    was_skipped=True,
                    duration_ms=int((time.monotonic() - start_time) * 1000),
                )

            # Step 2: Acquire in-process lock
            key = f"{workspace_id}/{unique_id}"
            lock = await self._limiter.get_lock(key)

            async with lock:
                # Re-check tier after acquiring lock (another coroutine may
                # have completed the download while we waited)
                probe = await SyncStateTracker.get_sync_probe_async(
                    workspace_id, unique_id
                )
                current_tier = probe.tier

                if current_tier >= required_tier:
                    return DownloadResult(
                        success=True,
                        achieved_tier=current_tier,
                        was_deduplicated=True,
                        duration_ms=int((time.monotonic() - start_time) * 1000),
                    )

                # Step 3: Perform download
                logger.info(
                    f"coordinator.download_started "
                    f"experiment={workspace_id}/{unique_id} "
                    f"tier={required_tier.name} "
                    f"caller={caller}"
                )

                if use_exclusive_lock:
                    achieved = await self._download_exclusive(
                        bucket_name, workspace_id, unique_id, required_tier
                    )
                elif required_tier == SyncTier.METADATA_ONLY:
                    achieved = await self._download_metadata_only(
                        bucket_name, workspace_id, unique_id
                    )
                else:
                    achieved = await self._download_standard(
                        bucket_name, workspace_id, unique_id, required_tier
                    )

                duration_ms = int((time.monotonic() - start_time) * 1000)

                # Step 4+5: Reconcile if needed
                if update_db_status and achieved >= SyncTier.ALL:
                    await asyncio.to_thread(
                        SyncStateTracker.reconcile,
                        workspace_id,
                        unique_id,
                        achieved,
                        bucket_name,
                    )

                logger.info(
                    f"coordinator.download_completed "
                    f"experiment={workspace_id}/{unique_id} "
                    f"tier={achieved.name} "
                    f"duration_ms={duration_ms} "
                    f"caller={caller}"
                )

                return DownloadResult(
                    success=True,
                    achieved_tier=achieved,
                    duration_ms=duration_ms,
                )

        except RemoteStorageLockError as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.warning(
                f"coordinator.exclusive_lock_held "
                f"experiment={workspace_id}/{unique_id} "
                f"caller={caller}"
            )
            return DownloadResult(
                success=False,
                achieved_tier=SyncTier.NONE,
                error=str(e),
                is_lock_error=True,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.error(
                f"coordinator.download_failed "
                f"experiment={workspace_id}/{unique_id} "
                f"tier={required_tier.name} "
                f"error={e} "
                f"caller={caller}",
                exc_info=True,
            )
            return DownloadResult(
                success=False,
                achieved_tier=SyncTier.NONE,
                error=str(e),
                duration_ms=duration_ms,
            )

    async def ensure_synced_batch(
        self,
        bucket_name: str,
        experiments: List[Tuple[str, str]],
        required_tier: SyncTier,
        concurrency: int = 5,
        caller: str = "",
    ) -> Dict[str, DownloadResult]:
        """Batch version with asyncio.Semaphore for concurrency control.

        Args:
            experiments: list of (workspace_id, unique_id) tuples
            concurrency: max concurrent downloads (match existing limits:
                         10 for thumbnails, 3 for metadata)
        """
        sem = asyncio.Semaphore(concurrency)
        results: Dict[str, DownloadResult] = {}

        async def _download_one(ws_id: str, uid: str):
            async with sem:
                try:
                    result = await self.ensure_synced(
                        bucket_name=bucket_name,
                        workspace_id=ws_id,
                        unique_id=uid,
                        required_tier=required_tier,
                        caller=caller,
                    )
                except Exception as e:
                    logger.warning(
                        f"coordinator.batch_item_failed "
                        f"experiment={ws_id}/{uid} "
                        f"tier={required_tier.name} "
                        f"error={e} "
                        f"caller={caller}"
                    )
                    result = DownloadResult(
                        success=False,
                        achieved_tier=SyncTier.NONE,
                        error=str(e),
                    )
                results[f"{ws_id}/{uid}"] = result

        await asyncio.gather(
            *[_download_one(ws_id, uid) for ws_id, uid in experiments],
        )

        return results

    async def ensure_metadata_available(
        self,
        bucket_name: str,
        workspace_id: str,
        db=None,
        caller: str = "records_page",
    ) -> None:
        """Compare DB experiment records against local filesystem.

        Download metadata for any experiments present in DB but missing locally.
        Used by the Records page to replace the all-or-nothing fallback.
        """
        if db is None:
            return

        try:
            from studio.app.common.core.experiment.experiment_reader import (
                ExptConfigReader,
            )

            # Get UIDs from DB
            db_uids = self._get_experiment_uids_from_db(db, workspace_id)
            if not db_uids:
                return

            # Get local UIDs from filesystem
            local_uids = ExptConfigReader.get_local_experiment_uids(workspace_id)

            # Find missing
            missing_uids = db_uids - local_uids
            if not missing_uids:
                return

            logger.info(
                f"coordinator.metadata_gap_detected "
                f"workspace_id={workspace_id} "
                f"missing_count={len(missing_uids)} "
                f"caller={caller}"
            )

            # Download metadata for missing experiments
            experiments = [(workspace_id, uid) for uid in missing_uids]
            await self.ensure_synced_batch(
                bucket_name=bucket_name,
                experiments=experiments,
                required_tier=SyncTier.METADATA_ONLY,
                concurrency=5,
                caller=caller,
            )

        except Exception as e:
            logger.error(
                f"coordinator.ensure_metadata_error "
                f"workspace_id={workspace_id} error={e}",
                exc_info=True,
            )

    @staticmethod
    def _get_experiment_uids_from_db(db, workspace_id: str) -> set:
        """Get set of experiment UIDs from DB for this workspace.

        Returns empty set if ExperimentRecordService is unavailable.
        """
        try:
            from studio.app.common.core.experiment.experiment_record_services import (
                ExperimentRecordService,
            )

            if not ExperimentRecordService.is_available():
                return set()

            from studio.app.common.models.experiment import ExperimentRecord

            records = (
                db.query(ExperimentRecord.uid)
                .filter(
                    ExperimentRecord.workspace_id == workspace_id,
                    ExperimentRecord.success == 1,
                )
                .all()
            )
            return {r[0] for r in records}

        except Exception:
            return set()

    async def _download_metadata_only(
        self,
        bucket_name: str,
        workspace_id: str,
        unique_id: str,
    ) -> SyncTier:
        """Download only metadata files (experiment.yaml, workflow.yaml)."""
        controller = RemoteStorageController(bucket_name)
        await controller.download_experiment_meta(workspace_id, unique_id)
        return SyncTier.METADATA_ONLY

    async def _download_standard(
        self,
        bucket_name: str,
        workspace_id: str,
        unique_id: str,
        tier: SyncTier,
    ) -> SyncTier:
        """Download via RemoteStorageController directly (non-exclusive)."""
        controller = RemoteStorageController(bucket_name)
        sync_mode = tier.to_sync_mode()
        await controller.download_experiment(
            workspace_id, unique_id, sync_mode=sync_mode
        )
        return tier

    async def _download_exclusive(
        self,
        bucket_name: str,
        workspace_id: str,
        unique_id: str,
        tier: SyncTier,
    ) -> SyncTier:
        """Download via RemoteStorageReader (exclusive lock).

        Used only for entry points #2 (user "Sync Remote") and
        #8 (background full sync).
        """
        async with RemoteStorageReader(
            bucket_name, workspace_id, unique_id
        ) as controller:
            sync_mode = tier.to_sync_mode()
            await controller.download_experiment(
                workspace_id, unique_id, sync_mode=sync_mode
            )
        return tier

    @staticmethod
    def _check_disk_space() -> bool:
        """Check if sufficient disk space is available (EC-5)."""
        try:
            from studio.app.dir_path import DIRPATH

            usage = shutil.disk_usage(DIRPATH.OUTPUT_DIR)
            if usage.free < _MIN_FREE_DISK_BYTES:
                logger.warning(
                    f"coordinator.disk_space_warning "
                    f"free_bytes={usage.free} "
                    f"threshold_bytes={_MIN_FREE_DISK_BYTES}"
                )
                return False
            return True
        except OSError:
            # If we can't check, proceed anyway
            return True
