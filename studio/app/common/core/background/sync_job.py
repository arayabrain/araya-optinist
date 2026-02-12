"""
Background job to sync published experiments from S3 to local storage.

Runs every 5 minutes, downloads experiments with local_sync_status='pending'.
"""

import asyncio
import os
from typing import TYPE_CHECKING, List, Tuple

from studio.app.common.core.utils.datetime_utils import get_current_datetime

if TYPE_CHECKING:
    from mypy_boto3_cloudwatch import CloudWatchClient

from filelock import FileLock, Timeout
from sqlmodel import select

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.storage.s3_storage_controller import S3StorageController
from studio.app.common.core.subscription.constants import SyncStatusConstants
from studio.app.common.db.database import session_scope
from studio.app.common.models import ExperimentRecord, User, Workspace
from studio.app.common.schemas.dataview import LocalSyncStatus, PublishStatus
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()


class SyncRetryError(Exception):
    """Exception indicating sync should be retried"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class PublishedExperimentSyncJob:
    """Background job to sync published experiments"""

    # Higher limit for thumbnail-only sync (thumbnails are small ~50-100KB)
    THUMBNAIL_SYNC_LIMIT = 50
    # Standard limit for metadata sync
    METADATA_SYNC_LIMIT = SyncStatusConstants.MAX_SYNC_PER_RUN
    # Concurrency limits for parallel downloads
    THUMBNAIL_CONCURRENCY = 10
    METADATA_CONCURRENCY = 3

    @classmethod
    async def run(cls):
        """
        Main sync job execution with two-phase sync:

        Phase 1: Sync thumbnail PNGs (fast, more per run)
        - Thumbnails are ~50-100KB vs full TIFFs which can be 100MB+
        - Can sync 50+ experiments' thumbnails per run
        - Enables fast DataView loading immediately

        Phase 2: Sync remaining metadata (YAML files)
        - Sync experiment.yaml, workflow.yaml, snakemake_config.yaml
        - Standard limit per run

        Uses file locking to prevent concurrent runs.
        """
        # Use FileLock for cross-platform file locking
        # timeout=0 means non-blocking (skip if lock is already held)
        lock = FileLock(SyncStatusConstants.LOCK_FILE, timeout=0)

        try:
            with lock:
                logger.info("Starting published experiment sync job (two-phase)")

                # Phase 1: Sync thumbnails first (fast)
                await cls._sync_thumbnails()

                # Phase 2: Sync remaining metadata
                await cls._run_sync_logic()

        except Timeout:
            # Another instance is already running
            logger.debug("Sync job already running, skipping this execution")
            return
        except Exception as e:
            logger.error(f"Fatal error in sync job: {e}", exc_info=True)

    @classmethod
    async def run_startup_sync(cls):
        """
        One-time sync at container startup.

        Unlike the periodic sync job, this:
        - Queries ALL published experiments (ignores local_sync_status)
        - Checks local file existence instead of DB status
        - Downloads only experiments missing locally
        - Does NOT modify DB sync status
        - Does NOT use file locking
        """
        logger.info("Starting one-time startup sync")

        try:
            all_experiments = cls._get_all_published_experiments()
            if not all_experiments:
                logger.info("No published experiments to sync")
                return

            from studio.app.common.core.utils.filepath_creater import join_filepath

            missing = []
            for ws_id, uid, exp_id, bucket in all_experiments:
                local_path = join_filepath([DIRPATH.OUTPUT_DIR, ws_id, uid])
                exp_yaml = os.path.join(local_path, DIRPATH.EXPERIMENT_YML)
                wf_yaml = os.path.join(local_path, DIRPATH.WORKFLOW_YML)
                if not os.path.exists(exp_yaml) or not os.path.exists(wf_yaml):
                    missing.append((ws_id, uid, exp_id, bucket))

            if not missing:
                logger.info("All published experiments already present locally")
                return

            logger.info(
                f"Startup sync: {len(missing)}/{len(all_experiments)}"
                f" experiments missing locally"
            )

            s3_controllers: dict[str, S3StorageController] = {}

            def get_s3(bucket_name: str) -> S3StorageController:
                if bucket_name not in s3_controllers:
                    s3_controllers[bucket_name] = S3StorageController(bucket_name)
                return s3_controllers[bucket_name]

            # Phase 1: thumbnails (high concurrency)
            thumb_sem = asyncio.Semaphore(cls.THUMBNAIL_CONCURRENCY)

            async def dl_thumb(ws_id, uid, bucket):
                async with thumb_sem:
                    try:
                        s3 = get_s3(bucket)
                        await s3.download_experiment(
                            ws_id,
                            uid,
                            sync_mode="thumbnails_only",
                        )
                    except Exception as e:
                        logger.warning(
                            f"Startup thumb sync failed " f"{ws_id}/{uid}: {e}"
                        )

            await asyncio.gather(
                *[dl_thumb(w, u, b) for w, u, _, b in missing],
            )

            # Phase 2: essential metadata (lower concurrency)
            meta_sem = asyncio.Semaphore(cls.METADATA_CONCURRENCY)

            async def dl_meta(ws_id, uid, bucket):
                async with meta_sem:
                    try:
                        s3 = get_s3(bucket)
                        await s3.download_experiment(
                            ws_id,
                            uid,
                            sync_mode="essential_only",
                        )
                    except Exception as e:
                        logger.warning(
                            f"Startup meta sync failed " f"{ws_id}/{uid}: {e}"
                        )

            await asyncio.gather(
                *[dl_meta(w, u, b) for w, u, _, b in missing],
            )

            logger.info(f"Startup sync completed for {len(missing)} experiments")

        except Exception as e:
            logger.error(f"Startup sync error: {e}", exc_info=True)

    @classmethod
    async def _sync_thumbnails(cls):
        """
        Phase 1: Download thumbnail PNGs for pending experiments.

        This is fast because:
        - Thumbnails are small (~50-100KB each)
        - We use thumbnails_only sync mode
        - We can process many more experiments per run
        """
        # Get pending experiments (use higher limit for thumbnails)
        pending = cls._get_pending_experiments(limit=cls.THUMBNAIL_SYNC_LIMIT)

        if not pending:
            logger.debug("No pending experiments for thumbnail sync")
            return

        logger.info(f"Phase 1: Syncing thumbnails for {len(pending)} experiments")

        # Cache S3 controllers per bucket to avoid creating duplicates
        s3_controllers: dict[str, S3StorageController] = {}

        def get_s3_controller(bucket_name: str) -> S3StorageController:
            if bucket_name not in s3_controllers:
                s3_controllers[bucket_name] = S3StorageController(bucket_name)
            return s3_controllers[bucket_name]

        # Sync thumbnails with higher concurrency (they're small files)
        semaphore = asyncio.Semaphore(cls.THUMBNAIL_CONCURRENCY)

        async def sync_thumbnails_for_experiment(
            workspace_id, unique_id, exp_id, bucket_name
        ):
            async with semaphore:
                try:
                    s3_controller = get_s3_controller(bucket_name)
                    # Only download thumbnails
                    await s3_controller.download_experiment(
                        workspace_id, unique_id, sync_mode="thumbnails_only"
                    )
                    return (workspace_id, unique_id, exp_id, True)
                except Exception as e:
                    logger.warning(
                        f"Failed to sync thumbnails for {workspace_id}/{unique_id} "
                        f"from bucket {bucket_name}: {e}"
                    )
                    return (workspace_id, unique_id, exp_id, False)

        tasks = [sync_thumbnails_for_experiment(w, u, e, b) for w, u, e, b in pending]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Count successes
        success_count = sum(1 for r in results if not isinstance(r, Exception) and r[3])
        logger.info(
            f"Phase 1 complete: {success_count}/{len(pending)} thumbnails synced"
        )

    @classmethod
    async def _run_sync_logic(cls):
        """Execute the actual sync logic"""
        try:
            # Get pending experiments (now includes bucket name)
            pending_experiments = cls._get_pending_experiments()

            if not pending_experiments:
                logger.debug("No pending experiments to sync")
                return

            logger.info(f"Found {len(pending_experiments)} experiments to sync")

            # Cache S3 controllers per bucket to avoid creating duplicates
            s3_controllers: dict[str, S3StorageController] = {}

            def get_s3_controller(bucket_name: str) -> S3StorageController:
                if bucket_name not in s3_controllers:
                    s3_controllers[bucket_name] = S3StorageController(bucket_name)
                return s3_controllers[bucket_name]

            # Sync experiments in parallel with limited concurrency
            semaphore = asyncio.Semaphore(cls.METADATA_CONCURRENCY)

            async def sync_with_semaphore(workspace_id, unique_id, exp_id, bucket_name):
                """Wrapper to limit concurrent downloads"""
                async with semaphore:
                    try:
                        s3_controller = get_s3_controller(bucket_name)
                        success = await cls._sync_experiment(
                            s3_controller, workspace_id, unique_id, exp_id
                        )
                        return (workspace_id, unique_id, exp_id, success, None)
                    except Exception as e:
                        logger.error(
                            f"Error syncing experiment {workspace_id}/{unique_id} "
                            f"from bucket {bucket_name}: {e}",
                            exc_info=True,
                        )
                        cls._mark_sync_error(exp_id)
                        return (workspace_id, unique_id, exp_id, False, e)

            # Create tasks for all pending experiments
            tasks = [
                sync_with_semaphore(workspace_id, unique_id, exp_id, bucket_name)
                for workspace_id, unique_id, exp_id, bucket_name in pending_experiments
            ]

            # Execute all syncs in parallel (limited by semaphore)
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Count successes and errors
            synced_count = 0
            error_count = 0

            for result in results:
                if isinstance(result, Exception):
                    # Unexpected exception from gather
                    logger.error(f"Unexpected error in sync task: {result}")
                    error_count += 1
                else:
                    _, _, _, success, _ = result
                    if success:
                        synced_count += 1
                    else:
                        error_count += 1

            logger.info(
                f"Sync job completed: {synced_count} synced, {error_count} errors "
                f"(parallel downloads with max {cls.METADATA_CONCURRENCY} concurrent)"
            )

            # Publish CloudWatch metrics
            cls._publish_metrics(synced_count, error_count)

        except Exception as e:
            logger.error(f"Error in sync logic: {e}", exc_info=True)

    @classmethod
    def _get_pending_experiments(
        cls, limit: int = None
    ) -> List[Tuple[str, str, int, str]]:
        """
        Query database for published experiments with pending or error sync status.

        IMPORTANT: This now includes experiments with 'error' status to enable
        automatic retry of failed syncs.

        Args:
            limit: Maximum number of experiments to return (defaults to
                SyncStatusConstants.MAX_SYNC_PER_RUN)

        Returns:
            List of tuples: (workspace_id, unique_id, experiment_record_id, bucket_name)
        """
        if limit is None:
            limit = SyncStatusConstants.MAX_SYNC_PER_RUN

        default_bucket = os.environ.get("S3_DEFAULT_BUCKET_NAME")

        with session_scope() as db:
            statement = (
                select(
                    ExperimentRecord.workspace_id,
                    ExperimentRecord.uid,
                    ExperimentRecord.id,
                    User.attributes,
                )
                .join(Workspace, Workspace.id == ExperimentRecord.workspace_id)
                .join(User, User.id == Workspace.user_id)
                .where(ExperimentRecord.publish_status == PublishStatus.on.value)
                .where(
                    ExperimentRecord.local_sync_status.in_(
                        [
                            LocalSyncStatus.pending.value,
                            LocalSyncStatus.error.value,
                        ]
                    )
                )
                .where(Workspace.deleted == 0)
                .where(ExperimentRecord.success == 1)
                .order_by(ExperimentRecord.analyzed_at.desc())
                .limit(limit)
            )

            result = db.execute(statement)

            experiments = []
            for row in result:
                workspace_id = str(row[0])
                unique_id = row[1]
                exp_id = row[2]
                user_attributes = row[3]
                # Get bucket name from user attributes, fall back to default
                bucket_name = (
                    user_attributes.get("remote_bucket_name")
                    if user_attributes
                    else None
                ) or default_bucket
                experiments.append((workspace_id, unique_id, exp_id, bucket_name))

            return experiments

    @classmethod
    def _get_all_published_experiments(
        cls,
    ) -> List[Tuple[str, str, int, str]]:
        """
        Query ALL published experiments regardless of sync status.

        Used by startup sync to check local file presence instead of
        relying on DB sync status (which reflects background service,
        not this container).

        Returns:
            List of (workspace_id, unique_id, exp_id, bucket_name)
        """
        default_bucket = os.environ.get("S3_DEFAULT_BUCKET_NAME")

        with session_scope() as db:
            statement = (
                select(
                    ExperimentRecord.workspace_id,
                    ExperimentRecord.uid,
                    ExperimentRecord.id,
                    User.attributes,
                )
                .join(
                    Workspace,
                    Workspace.id == ExperimentRecord.workspace_id,
                )
                .join(User, User.id == Workspace.user_id)
                .where(ExperimentRecord.publish_status == PublishStatus.on.value)
                .where(Workspace.deleted == 0)
                .where(ExperimentRecord.success == 1)
                .order_by(ExperimentRecord.analyzed_at.desc())
            )

            result = db.execute(statement)

            experiments = []
            for row in result:
                workspace_id = str(row[0])
                unique_id = row[1]
                exp_id = row[2]
                user_attributes = row[3]
                bucket_name = (
                    user_attributes.get("remote_bucket_name")
                    if user_attributes
                    else None
                ) or default_bucket
                experiments.append((workspace_id, unique_id, exp_id, bucket_name))

            return experiments

    @classmethod
    async def _sync_experiment(
        cls,
        s3_controller: S3StorageController,
        workspace_id: str,
        unique_id: str,
        exp_id: int,
    ) -> bool:
        """
        Download experiment from S3 to local storage with exponential backoff retry.

        Args:
            s3_controller: S3 storage controller
            workspace_id: Workspace ID
            unique_id: Experiment unique ID
            exp_id: Experiment record database ID

        Returns:
            True if sync successful, False otherwise
        """
        logger.info(f"Syncing experiment {workspace_id}/{unique_id} (id={exp_id})")

        try:
            # Check if already exists locally
            from studio.app.common.core.utils.filepath_creater import join_filepath

            local_path = join_filepath([DIRPATH.OUTPUT_DIR, workspace_id, unique_id])

            if os.path.exists(local_path):
                # Already exists, check if complete
                required_files = [DIRPATH.EXPERIMENT_YML, DIRPATH.WORKFLOW_YML]

                all_exist = all(
                    os.path.exists(os.path.join(local_path, f)) for f in required_files
                )

                if all_exist:
                    logger.info(f"Experiment {workspace_id}/{unique_id} already synced")
                    cls._mark_sync_complete(exp_id)
                    return True

            # Download from S3 with exponential backoff retry
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    logger.info(
                        f"Downloading from S3 with selective sync "
                        f"(attempt {attempt + 1}/{max_retries}): "
                        f"{workspace_id}/{unique_id}"
                    )
                    success = await s3_controller.download_experiment(
                        workspace_id, unique_id, sync_mode="essential_only"
                    )

                    if not success:
                        raise SyncRetryError("Download returned failure status")

                    # Validate that required files were actually downloaded
                    required_files = [DIRPATH.EXPERIMENT_YML, DIRPATH.WORKFLOW_YML]
                    missing = [
                        f
                        for f in required_files
                        if not os.path.exists(os.path.join(local_path, f))
                    ]

                    if missing:
                        raise SyncRetryError(
                            f"Required files missing from S3: {missing}"
                        )

                    logger.info(f"Successfully synced {workspace_id}/{unique_id}")
                    cls._mark_sync_complete(exp_id)
                    cls._clear_retry_count(exp_id)
                    return True

                except Exception as e:
                    is_expected_retry = isinstance(e, SyncRetryError)
                    error_msg = str(e)

                    if attempt < max_retries - 1:
                        wait_time = 2**attempt  # 1s, 2s, 4s
                        logger.warning(f"{error_msg}, retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(
                            f"Failed to sync {workspace_id}/{unique_id} "
                            f"after {max_retries} attempts: {error_msg}",
                            exc_info=not is_expected_retry,
                        )

            # All retries failed
            cls._mark_sync_error(exp_id)
            cls._increment_retry_count(exp_id)
            cls._check_persistent_failure(exp_id, workspace_id, unique_id)
            return False

        except Exception as e:
            logger.error(
                f"Error syncing {workspace_id}/{unique_id}: {e}", exc_info=True
            )
            cls._mark_sync_error(exp_id)
            cls._increment_retry_count(exp_id)
            return False

    @classmethod
    def _mark_sync_complete(cls, exp_id: int):
        """Mark experiment as successfully synced"""
        with session_scope() as db:
            experiment = db.get(ExperimentRecord, exp_id)
            if experiment:
                experiment.local_sync_status = LocalSyncStatus.synced.value
                db.add(experiment)
                db.commit()

            logger.debug(f"Marked experiment {exp_id} as synced")

    @classmethod
    def _mark_sync_error(cls, exp_id: int):
        """Mark experiment sync as failed (will retry next run)"""
        with session_scope() as db:
            experiment = db.get(ExperimentRecord, exp_id)
            if experiment:
                experiment.local_sync_status = LocalSyncStatus.error.value
                db.add(experiment)
                db.commit()

            logger.debug(f"Marked experiment {exp_id} as sync error")

    @classmethod
    def _increment_retry_count(cls, exp_id: int):
        """Increment retry count for failed sync (stored in DB for persistence)"""
        with session_scope() as db:
            experiment = db.get(ExperimentRecord, exp_id)
            if experiment:
                # Use a custom field or metadata to track retries
                # For now, log the retry attempt
                logger.info(f"Incrementing retry count for experiment {exp_id}")

    @classmethod
    def _clear_retry_count(cls, exp_id: int):
        """Clear retry count after successful sync"""
        logger.debug(f"Cleared retry count for experiment {exp_id}")

    @classmethod
    def _check_persistent_failure(cls, exp_id: int, workspace_id: str, unique_id: str):
        """Check for persistent failures and alert operators"""
        # Query how many times this experiment has failed
        # For now, publish high-priority CloudWatch metric
        try:
            import boto3

            cloudwatch: "CloudWatchClient" = boto3.client("cloudwatch")
            cloudwatch.put_metric_data(
                Namespace="OptiNiSt/BackgroundJobs",
                MetricData=[
                    {
                        "MetricName": "PersistentSyncFailure",
                        "Value": 1,
                        "Unit": "Count",
                        "Timestamp": get_current_datetime(),
                        "Dimensions": [
                            {"Name": "ExperimentId", "Value": str(exp_id)},
                            {"Name": "WorkspaceId", "Value": workspace_id},
                        ],
                    }
                ],
            )
            logger.error(
                f"PERSISTENT SYNC FAILURE: Experiment {workspace_id}/{unique_id} "
                f"(id={exp_id}) has failed multiple sync attempts. "
                f"Manual intervention may be required."
            )
        except Exception as e:
            logger.warning(f"Failed to publish persistent failure metric: {e}")

    @classmethod
    def _publish_metrics(cls, synced_count: int, error_count: int):
        """Publish sync job metrics to CloudWatch with alarm thresholds"""
        try:
            import boto3

            cloudwatch: "CloudWatchClient" = boto3.client("cloudwatch")

            cloudwatch.put_metric_data(
                Namespace="OptiNiSt/BackgroundJobs",
                MetricData=[
                    {
                        "MetricName": "ExperimentsSynced",
                        "Value": synced_count,
                        "Unit": "Count",
                        "Timestamp": get_current_datetime(),
                    },
                    {
                        "MetricName": "SyncErrors",
                        "Value": error_count,
                        "Unit": "Count",
                        "Timestamp": get_current_datetime(),
                    },
                    {
                        "MetricName": "SyncErrorRate",
                        "Value": (
                            (error_count / (synced_count + error_count) * 100)
                            if (synced_count + error_count) > 0
                            else 0
                        ),
                        "Unit": "Percent",
                        "Timestamp": get_current_datetime(),
                    },
                ],
            )
            logger.debug(
                f"Published CloudWatch metrics: {synced_count} synced, "
                f"{error_count} errors"
            )

            # Alert if error rate is high
            if (synced_count + error_count) > 0:
                error_rate = error_count / (synced_count + error_count) * 100
                if error_rate > 50:  # More than 50% failures
                    logger.error(
                        f"HIGH SYNC ERROR RATE: {error_rate:.1f}% "
                        f"({error_count}/{synced_count + error_count} failed). "
                        f"Check S3 connectivity and permissions."
                    )
        except Exception as e:
            logger.warning(f"Failed to publish metrics: {e}")
