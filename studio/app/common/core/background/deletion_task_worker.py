"""
Background worker to process deletion tasks.
Ensures deletions complete even if user logs out.

This worker processes queued deletion tasks independently of user sessions.
It runs periodically and picks up queued/retrying tasks.
"""
import asyncio
from typing import Optional

from studio.app.common.core.experiment.deletion_task_service import DeletionTaskService
from studio.app.common.core.experiment.experiment_record_services import (
    ExperimentRecordService,
)
from studio.app.common.core.experiment.experiment_writer import ExptDataWriter
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    RemoteSyncLockFileUtil,
)
from studio.app.common.core.workspace.workspace_services import WorkspaceService
from studio.app.common.db.database import session_scope
from studio.app.common.models.experiment import DeletionTaskType
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()

DELETION_WORKER_BATCH_SIZE = 5
DELETION_WORKER_DELAY_SECONDS = 1.0


class DeletionTaskWorker:
    """Background worker to process deletion tasks from the queue."""

    @classmethod
    async def run(cls, batch_size: int = DELETION_WORKER_BATCH_SIZE) -> dict:
        """
        Process pending deletion tasks.
        Called periodically by the background job scheduler.

        Args:
            batch_size: Maximum number of tasks to process per run

        Returns:
            Dict with processing statistics
        """
        logger.info("Starting deletion task worker")

        stats = {
            "processed": 0,
            "completed": 0,
            "failed": 0,
            "retrying": 0,
        }

        try:
            tasks = DeletionTaskService.get_pending_tasks(limit=batch_size)

            if not tasks:
                logger.debug("No pending deletion tasks")
                return stats

            logger.info(f"Processing {len(tasks)} deletion tasks")

            for task in tasks:
                try:
                    stats["processed"] += 1
                    success = await cls._process_task(task)

                    if success:
                        stats["completed"] += 1
                    else:
                        # Check if task is retrying or failed
                        task_status = DeletionTaskService.get_task_status(task.id)
                        if task_status and task_status["status"] == "failed":
                            stats["failed"] += 1
                        else:
                            stats["retrying"] += 1

                    # Small delay between tasks to avoid overwhelming the system
                    await asyncio.sleep(DELETION_WORKER_DELAY_SECONDS)

                except Exception as e:
                    logger.error(f"Error processing task {task.id}: {e}")
                    DeletionTaskService.mark_failed(task.id, str(e))
                    stats["failed"] += 1

            logger.info(
                f"Deletion task worker complete: processed={stats['processed']}, "
                f"completed={stats['completed']}, failed={stats['failed']}, "
                f"retrying={stats['retrying']}"
            )

        except Exception as e:
            logger.error(f"Deletion task worker failed: {e}")

        return stats

    @classmethod
    async def _process_task(cls, task) -> bool:
        """
        Process a single deletion task.

        Args:
            task: DeletionTask record to process

        Returns:
            True if deletion completed successfully
        """
        logger.info(
            f"Processing deletion task {task.id}: type={task.task_type}, "
            f"resource={task.resource_id}"
        )

        # Mark as in progress
        DeletionTaskService.mark_in_progress(task.id)

        try:
            if task.task_type == DeletionTaskType.EXPERIMENT.value:
                success = await cls._delete_experiment(
                    workspace_id=task.workspace_id,
                    experiment_uid=task.resource_id,
                )
            elif task.task_type == DeletionTaskType.WORKSPACE.value:
                success = await cls._delete_workspace(
                    workspace_id=int(task.resource_id),
                    user_id=task.user_id,
                )
            else:
                logger.error(f"Unknown task type: {task.task_type}")
                DeletionTaskService.mark_failed(task.id, "Unknown task type")
                return False

            if success:
                DeletionTaskService.mark_completed(task.id)
                return True
            else:
                DeletionTaskService.mark_failed(
                    task.id, "Deletion returned False (partial failure)"
                )
                return False

        except Exception as e:
            error_msg = str(e)[:500]
            logger.error(f"Task {task.id} deletion failed: {error_msg}")
            DeletionTaskService.mark_failed(task.id, error_msg)
            return False

    @classmethod
    async def _delete_experiment(
        cls,
        workspace_id: int,
        experiment_uid: str,
    ) -> bool:
        """
        Delete an experiment (S3 + local + DB record).

        Args:
            workspace_id: Workspace containing the experiment
            experiment_uid: Unique ID of experiment to delete

        Returns:
            True if deletion completed successfully
        """
        try:
            # Get the remote bucket name
            remote_bucket_name = cls._get_remote_bucket_name()

            if RemoteStorageController.is_available():
                # Check for remote-sync-lock-file
                try:
                    RemoteSyncLockFileUtil.check_sync_lock_file(
                        str(workspace_id), experiment_uid, raise_error=True
                    )
                except Exception as lock_error:
                    logger.warning(
                        f"Sync lock exists for experiment {experiment_uid}: "
                        f"{lock_error}"
                    )
                    return False  # Will retry later

            # Delete experiment data (S3 and local filesystem)
            s3_success = await ExptDataWriter(
                remote_bucket_name, str(workspace_id), experiment_uid
            ).delete_data()

            # Delete database record
            if ExperimentRecordService.is_available():
                with session_scope() as db:
                    try:
                        ExperimentRecordService.delete_record(
                            db, str(workspace_id), experiment_uid, auto_commit=True
                        )
                    except Exception as db_error:
                        if s3_success:
                            # S3 deleted but DB failed - mark as orphaned
                            logger.error(
                                f"DB deletion failed after S3 deletion: {db_error}"
                            )
                            ExperimentRecordService.mark_as_orphaned(
                                db,
                                str(workspace_id),
                                experiment_uid,
                                error_message=f"Queued deletion: S3 deleted, "
                                f"DB error: {str(db_error)[:200]}",
                            )
                            db.commit()
                            return False
                        raise

            return s3_success

        except Exception as e:
            logger.error(f"Experiment deletion failed: {e}")
            raise

    @classmethod
    async def _delete_workspace(
        cls,
        workspace_id: int,
        user_id: int,
    ) -> bool:
        """
        Delete a workspace and all its experiments.

        Args:
            workspace_id: Workspace to delete
            user_id: User who owns the workspace

        Returns:
            True if deletion completed successfully
        """
        try:
            with session_scope() as db:
                success, message = await WorkspaceService.process_workspace_deletion(
                    db=db,
                    workspace_id=workspace_id,
                    user_id=user_id,
                )
                return success

        except Exception as e:
            logger.error(f"Workspace deletion failed: {e}")
            raise

    @classmethod
    def _get_remote_bucket_name(cls) -> Optional[str]:
        """Get the remote S3 bucket name from configuration."""
        try:
            return DIRPATH.DATA_BUCKET_NAME
        except Exception:
            return None
