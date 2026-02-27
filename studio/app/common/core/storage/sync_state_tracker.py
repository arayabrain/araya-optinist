import asyncio
import os
from dataclasses import dataclass
from typing import Optional

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteSyncAction,
    RemoteSyncStatus,
    RemoteSyncStatusFileUtil,
)
from studio.app.common.core.storage.sync_tier import SyncTier
from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()


@dataclass
class SyncProbeResult:
    """Cross-checked snapshot of an experiment's sync state from all sources.

    Named to distinguish from the four existing sync status types:
    - SyncStatus in schemas/files.py
    - SyncStatus in subscription/constants.py
    - LocalSyncStatus in schemas/dataview.py
    - RemoteSyncStatus in remote_storage_controller.py
    """

    tier: SyncTier  # inferred from files present on disk
    file_status: Optional[RemoteSyncStatus]  # from remote_sync_stat.json


class SyncStateTracker:
    """Reconciles DB local_sync_status and file remote_sync_stat.json.

    Provides a single source of truth for experiment sync state.
    """

    @classmethod
    def get_sync_probe(
        cls,
        workspace_id: str,
        unique_id: str,
    ) -> SyncProbeResult:
        """Cross-check filesystem and status file to determine true sync state.

        Always checks filesystem fresh (never cached) to avoid EC-4.
        """
        # Check file-based sync status
        file_status = RemoteSyncStatusFileUtil.check_sync_status_file(
            workspace_id, unique_id
        )

        # Detect current tier from filesystem
        tier = cls._detect_tier_from_filesystem(workspace_id, unique_id)

        return SyncProbeResult(
            tier=tier,
            file_status=file_status,
        )

    @classmethod
    async def get_sync_probe_async(
        cls,
        workspace_id: str,
        unique_id: str,
    ) -> SyncProbeResult:
        """Async version that offloads file I/O to thread pool (EC-6)."""
        return await asyncio.to_thread(cls.get_sync_probe, workspace_id, unique_id)

    @classmethod
    def _detect_tier_from_filesystem(
        cls,
        workspace_id: str,
        unique_id: str,
    ) -> SyncTier:
        """Inspect local filesystem to determine what data is present.

        Returns the highest tier where ALL required marker files exist.
        Each tier requires ALL files (not ANY) to avoid EC-14.

        This must be called fresh each time, never cached. A previous
        download may have failed mid-tier, leaving partial files.
        """
        experiment_dir = join_filepath([DIRPATH.OUTPUT_DIR, workspace_id, unique_id])

        if not os.path.isdir(experiment_dir):
            return SyncTier.NONE

        # METADATA_ONLY: experiment.yaml AND workflow.yaml must both exist
        experiment_yaml = os.path.join(experiment_dir, "experiment.yaml")
        workflow_yaml = os.path.join(experiment_dir, "workflow.yaml")

        if not (os.path.isfile(experiment_yaml) and os.path.isfile(workflow_yaml)):
            return SyncTier.NONE

        # Check for thumbnails (at least one PNG)
        has_thumbnails = False
        with os.scandir(experiment_dir) as entries:
            for entry in entries:
                if entry.is_file() and entry.name.lower().endswith(".png"):
                    has_thumbnails = True
                    break

        if not has_thumbnails:
            return SyncTier.METADATA_ONLY

        # ESSENTIAL_ONLY: + snakemake_config.yaml
        snakemake_config = os.path.join(experiment_dir, "snakemake_config.yaml")
        if not os.path.isfile(snakemake_config):
            return SyncTier.THUMBNAILS_ONLY

        # Check for JSON output files in function subdirectories
        has_json_outputs = False
        with os.scandir(experiment_dir) as entries:
            for entry in entries:
                if entry.is_dir() and not entry.name.startswith("."):
                    with os.scandir(entry.path) as sub_entries:
                        for sub_entry in sub_entries:
                            if sub_entry.is_file() and sub_entry.name.endswith(".json"):
                                has_json_outputs = True
                                break
                    if has_json_outputs:
                        break

        if not has_json_outputs:
            return SyncTier.THUMBNAILS_ONLY

        # VISUALIZATION: check for input TIFF/CSV files
        # Requires reading snakemake_config.yaml to know expected inputs
        try:
            from studio.app.common.core.snakemake.smk_utils import SmkUtils

            input_filenames = SmkUtils.get_datatypes_inputs(
                workspace_id, unique_id, apply_basename=True
            )
            if input_filenames:
                input_dir = join_filepath([DIRPATH.DATA_DIR, "input", workspace_id])
                all_inputs_present = True
                for filename in input_filenames:
                    input_path = os.path.join(input_dir, filename)
                    if not os.path.isfile(input_path):
                        all_inputs_present = False
                        break
                if not all_inputs_present:
                    return SyncTier.ESSENTIAL_ONLY
        except Exception:
            # Config not readable -- can't determine VISUALIZATION tier
            return SyncTier.ESSENTIAL_ONLY

        # ALL: + remote_sync_stat.json with SUCCESS status
        if RemoteSyncStatusFileUtil.check_sync_status_success(workspace_id, unique_id):
            return SyncTier.ALL

        return SyncTier.VISUALIZATION

    @classmethod
    def invalidate_stale_records(cls) -> int:
        """Find DB records where local_sync_status='synced' but experiment
        files are missing from local EBS. Reset those to 'pending'.

        Uses cursor-based pagination (batches of 500) to avoid OOM (EC-20).
        Creates its own session via session_scope() (same pattern as sync_job.py).

        Returns number of records invalidated.
        """
        from studio.app.common.db.database import session_scope
        from studio.app.common.models.experiment import ExperimentRecord
        from studio.app.common.schemas.dataview import LocalSyncStatus, PublishStatus

        invalidated_count = 0
        batch_size = 500
        last_id = 0

        while True:
            with session_scope() as db:
                records = (
                    db.query(ExperimentRecord)
                    .filter(
                        ExperimentRecord.id > last_id,
                        ExperimentRecord.local_sync_status
                        == LocalSyncStatus.synced.value,
                        ExperimentRecord.publish_status == PublishStatus.on.value,
                    )
                    .order_by(ExperimentRecord.id)
                    .limit(batch_size)
                    .all()
                )

                if not records:
                    break

                for record in records:
                    last_id = record.id

                    experiment_dir = join_filepath(
                        [DIRPATH.OUTPUT_DIR, str(record.workspace_id), record.uid]
                    )
                    experiment_yaml = os.path.join(experiment_dir, "experiment.yaml")
                    workflow_yaml = os.path.join(experiment_dir, "workflow.yaml")

                    # Invalidate if the directory doesn't exist or either
                    # key metadata file is missing (matches _detect_tier_from_filesystem
                    # which requires BOTH files for METADATA_ONLY tier)
                    if not os.path.isdir(experiment_dir) or (
                        not os.path.isfile(experiment_yaml)
                        or not os.path.isfile(workflow_yaml)
                    ):
                        record.local_sync_status = LocalSyncStatus.pending.value
                        invalidated_count += 1
                        logger.info(
                            f"coordinator.stale_record_invalidated "
                            f"workspace_id={record.workspace_id} "
                            f"uid={record.uid}"
                        )

        logger.info(f"coordinator.stale_records_invalidated count={invalidated_count}")
        return invalidated_count

    @classmethod
    def reconcile(
        cls,
        workspace_id: str,
        unique_id: str,
        achieved_tier: SyncTier,
        bucket_name: str,
    ) -> None:
        """Ensure DB and file status agree after a download completes.

        Creates its own session via session_scope().

        For full syncs (tier=ALL):
        - Set remote_sync_stat.json -> SUCCESS (if not already)
        - Set DB local_sync_status -> 'synced' (if record exists)

        For partial syncs: no-op (partial state is not tracked in either system).
        """
        if achieved_tier != SyncTier.ALL:
            return

        # Ensure file status is SUCCESS
        if not RemoteSyncStatusFileUtil.check_sync_status_success(
            workspace_id, unique_id
        ):
            RemoteSyncStatusFileUtil.create_sync_status_file_for_success(
                bucket_name,
                workspace_id,
                unique_id,
                RemoteSyncAction.DOWNLOAD,
            )

        # Update DB status
        try:
            from studio.app.common.db.database import session_scope
            from studio.app.common.models.experiment import ExperimentRecord
            from studio.app.common.schemas.dataview import LocalSyncStatus

            with session_scope() as db:
                record = (
                    db.query(ExperimentRecord)
                    .filter(
                        ExperimentRecord.workspace_id == workspace_id,
                        ExperimentRecord.uid == unique_id,
                    )
                    .first()
                )
                if record and record.local_sync_status != LocalSyncStatus.synced.value:
                    record.local_sync_status = LocalSyncStatus.synced.value
                    logger.info(
                        f"coordinator.reconcile_db_updated "
                        f"workspace_id={workspace_id} uid={unique_id}"
                    )
        except Exception as e:
            # DB may not be available (standalone mode)
            logger.debug(f"Reconcile DB update skipped: {e}")

    @classmethod
    def check_synced_staleness_spot_check(cls, sample_size: int = 10) -> int:
        """Spot-check a small random sample of 'synced' experiments to verify
        local files still exist. If missing, reset to 'pending'.

        Runs periodically (every 5 minutes) on API containers only.
        Only checks sample_size records, so cost is negligible.

        Uses count + random offset to avoid loading all records (EC-20).

        Returns number of records invalidated.
        """
        import random

        from sqlalchemy import func

        from studio.app.common.db.database import session_scope
        from studio.app.common.models.experiment import ExperimentRecord
        from studio.app.common.schemas.dataview import LocalSyncStatus, PublishStatus

        invalidated_count = 0

        try:
            with session_scope() as db:
                base_filter = [
                    ExperimentRecord.local_sync_status == LocalSyncStatus.synced.value,
                    ExperimentRecord.publish_status == PublishStatus.on.value,
                ]

                total_count = (
                    db.query(func.count(ExperimentRecord.id))
                    .filter(*base_filter)
                    .scalar()
                )

                if not total_count:
                    return 0

                # Pick random offsets to sample without loading all rows
                actual_sample = min(sample_size, total_count)
                offsets = random.sample(range(total_count), actual_sample)

                for offset in offsets:
                    record = (
                        db.query(ExperimentRecord)
                        .filter(*base_filter)
                        .order_by(ExperimentRecord.id)
                        .offset(offset)
                        .limit(1)
                        .first()
                    )

                    if not record:
                        continue

                    experiment_dir = join_filepath(
                        [DIRPATH.OUTPUT_DIR, str(record.workspace_id), record.uid]
                    )
                    experiment_yaml = os.path.join(experiment_dir, "experiment.yaml")
                    workflow_yaml = os.path.join(experiment_dir, "workflow.yaml")

                    if not os.path.isdir(experiment_dir) or (
                        not os.path.isfile(experiment_yaml)
                        or not os.path.isfile(workflow_yaml)
                    ):
                        record.local_sync_status = LocalSyncStatus.pending.value
                        invalidated_count += 1
                        logger.warning(
                            f"coordinator.spot_check_stale "
                            f"workspace_id={record.workspace_id} "
                            f"uid={record.uid}"
                        )

        except Exception as e:
            logger.debug(f"Staleness spot-check skipped: {e}")

        if invalidated_count:
            logger.info(f"coordinator.spot_check_invalidated count={invalidated_count}")
        return invalidated_count
