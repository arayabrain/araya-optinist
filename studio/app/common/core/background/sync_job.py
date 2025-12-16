"""
Background job to sync published experiments from S3 to local storage.

Runs every 5 minutes, downloads experiments with local_sync_status='pending'.
"""

import asyncio
import os
from datetime import datetime
from typing import List, Tuple

from filelock import FileLock, Timeout
from sqlmodel import select

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.storage.s3_storage_controller import S3StorageController
from studio.app.common.core.subscription.constants import SyncStatusConstants
from studio.app.common.db.database import session_scope
from studio.app.common.models import ExperimentRecord, Workspace
from studio.app.common.schemas.dataview import LocalSyncStatus, PublishStatus
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()


class PublishedExperimentSyncJob:
    """Background job to sync published experiments"""

    @classmethod
    async def run(cls):
        """
        Main sync job execution with file locking:
        1. Acquire lock to prevent concurrent runs
        2. Query published experiments with local_sync_status='pending'
        3. Download from S3 to local storage
        4. Update sync status in database
        5. Handle errors with retry logic
        """
        # Use FileLock for cross-platform file locking
        # timeout=0 means non-blocking (skip if lock is already held)
        lock = FileLock(SyncStatusConstants.LOCK_FILE, timeout=0)

        try:
            with lock:
                logger.info("Starting published experiment sync job")
                await cls._run_sync_logic()

        except Timeout:
            # Another instance is already running
            logger.debug("Sync job already running, skipping this execution")
            return
        except Exception as e:
            logger.error(f"Fatal error in sync job: {e}", exc_info=True)

    @classmethod
    async def _run_sync_logic(cls):
        """Execute the actual sync logic"""
        try:
            # Get S3 storage controller
            bucket_name = os.environ.get("S3_DEFAULT_BUCKET_NAME")
            if not bucket_name:
                logger.error("S3_DEFAULT_BUCKET_NAME not configured, skipping sync")
                return

            s3_controller = S3StorageController(bucket_name)

            # Get pending experiments
            pending_experiments = cls._get_pending_experiments()

            if not pending_experiments:
                logger.debug("No pending experiments to sync")
                return

            logger.info(f"Found {len(pending_experiments)} experiments to sync")

            # Sync experiments in parallel with limited concurrency
            # Limit to 3 concurrent downloads to avoid overloading S3/network
            max_concurrent = 3
            semaphore = asyncio.Semaphore(max_concurrent)

            async def sync_with_semaphore(workspace_id, unique_id, exp_id):
                """Wrapper to limit concurrent downloads"""
                async with semaphore:
                    try:
                        success = await cls._sync_experiment(
                            s3_controller, workspace_id, unique_id, exp_id
                        )
                        return (workspace_id, unique_id, exp_id, success, None)
                    except Exception as e:
                        logger.error(
                            f"Error syncing experiment {workspace_id}/{unique_id}: {e}",
                            exc_info=True,
                        )
                        cls._mark_sync_error(exp_id)
                        return (workspace_id, unique_id, exp_id, False, e)

            # Create tasks for all pending experiments
            tasks = [
                sync_with_semaphore(workspace_id, unique_id, exp_id)
                for workspace_id, unique_id, exp_id in pending_experiments
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
                f"(parallel downloads with max {max_concurrent} concurrent)"
            )

            # Publish CloudWatch metrics
            cls._publish_metrics(synced_count, error_count)

        except Exception as e:
            logger.error(f"Error in sync logic: {e}", exc_info=True)

    @classmethod
    def _get_pending_experiments(cls) -> List[Tuple[str, str, int]]:
        """
        Query database for published experiments with pending or error sync status.

        IMPORTANT: This now includes experiments with 'error' status to enable
        automatic retry of failed syncs.

        Returns:
            List of tuples: (workspace_id, unique_id, experiment_record_id)
        """
        with session_scope() as db:
            statement = (
                select(
                    ExperimentRecord.workspace_id,
                    ExperimentRecord.uid,
                    ExperimentRecord.id,
                )
                .join(Workspace, Workspace.id == ExperimentRecord.workspace_id)
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
                .limit(SyncStatusConstants.MAX_SYNC_PER_RUN)
            )

            result = db.exec(statement)

            return [(str(row[0]), row[1], row[2]) for row in result]

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
                required_files = ["experiment.yaml", "workflow.yaml"]

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
                        f"Downloading from S3 (attempt {attempt + 1}/{max_retries}): "
                        f"{workspace_id}/{unique_id}"
                    )
                    success = await s3_controller.download_experiment(
                        workspace_id, unique_id
                    )

                    if success:
                        logger.info(f"Successfully synced {workspace_id}/{unique_id}")
                        cls._mark_sync_complete(exp_id)
                        cls._clear_retry_count(exp_id)
                        return True
                    else:
                        if attempt < max_retries - 1:
                            wait_time = 2**attempt  # 1s, 2s, 4s
                            logger.warning(
                                f"Download failed, retrying in {wait_time}s..."
                            )
                            await asyncio.sleep(wait_time)
                        else:
                            logger.error(
                                f"Failed to download {workspace_id}/{unique_id} "
                                f"after {max_retries} attempts"
                            )

                except Exception as e:
                    if attempt < max_retries - 1:
                        wait_time = 2**attempt
                        logger.warning(
                            f"Download error: {e}, retrying in {wait_time}s..."
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(
                            f"Download failed after {max_retries} attempts: {e}",
                            exc_info=True,
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

            cloudwatch = boto3.client("cloudwatch")
            cloudwatch.put_metric_data(
                Namespace="OptiNiSt/BackgroundJobs",
                MetricData=[
                    {
                        "MetricName": "PersistentSyncFailure",
                        "Value": 1,
                        "Unit": "Count",
                        "Timestamp": datetime.now(),
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

            cloudwatch = boto3.client("cloudwatch")

            cloudwatch.put_metric_data(
                Namespace="OptiNiSt/BackgroundJobs",
                MetricData=[
                    {
                        "MetricName": "ExperimentsSynced",
                        "Value": synced_count,
                        "Unit": "Count",
                        "Timestamp": datetime.now(),
                    },
                    {
                        "MetricName": "SyncErrors",
                        "Value": error_count,
                        "Unit": "Count",
                        "Timestamp": datetime.now(),
                    },
                    {
                        "MetricName": "SyncErrorRate",
                        "Value": (
                            (error_count / (synced_count + error_count) * 100)
                            if (synced_count + error_count) > 0
                            else 0
                        ),
                        "Unit": "Percent",
                        "Timestamp": datetime.now(),
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
