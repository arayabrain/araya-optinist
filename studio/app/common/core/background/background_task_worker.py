"""
Background worker to process tasks from the queue.
Ensures tasks complete even if user logs out.

This worker processes queued tasks independently of user sessions.
It runs periodically and picks up queued/retrying tasks.
"""
import asyncio
from typing import Optional

from studio.app.common.core.experiment.background_task_service import (
    BackgroundTaskService,
)
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
from studio.app.common.models.experiment import BackgroundTaskType
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()

BACKGROUND_WORKER_BATCH_SIZE = 5
BACKGROUND_WORKER_DELAY_SECONDS = 1.0


class BackgroundTaskWorker:
    """Background worker to process tasks from the queue."""

    @classmethod
    async def run(cls, batch_size: int = BACKGROUND_WORKER_BATCH_SIZE) -> dict:
        """
        Process pending background tasks.
        Called periodically by the background job scheduler.

        Returns:
            Dict with processing statistics
        """
        logger.info("Starting background task worker")

        stats = {
            "processed": 0,
            "completed": 0,
            "failed": 0,
            "retrying": 0,
        }

        try:
            tasks = BackgroundTaskService.get_pending_tasks(limit=batch_size)

            if not tasks:
                logger.debug("No pending background tasks")
                return stats

            logger.info(f"Processing {len(tasks)} background tasks")

            for task in tasks:
                try:
                    stats["processed"] += 1
                    success = await cls._process_task(task)

                    if success:
                        stats["completed"] += 1
                    else:
                        task_status = BackgroundTaskService.get_task_status(task.id)
                        if task_status and task_status["status"] == "failed":
                            stats["failed"] += 1
                        else:
                            stats["retrying"] += 1

                    await asyncio.sleep(BACKGROUND_WORKER_DELAY_SECONDS)

                except Exception as e:
                    logger.error(f"Error processing task {task.id}: {e}")
                    BackgroundTaskService.mark_failed(task.id, str(e))
                    stats["failed"] += 1

            logger.info(
                "Background task worker complete: "
                f"processed={stats['processed']}, "
                f"completed={stats['completed']}, "
                f"failed={stats['failed']}, "
                f"retrying={stats['retrying']}"
            )

        except Exception as e:
            logger.error(f"Background task worker failed: {e}")

        return stats

    @classmethod
    async def _process_task(cls, task) -> bool:
        """
        Process a single background task.

        Returns:
            True if task completed successfully
        """
        logger.info(
            f"Processing background task {task.id}: "
            f"type={task.task_type}, "
            f"resource={task.resource_id}"
        )

        BackgroundTaskService.mark_in_progress(task.id)

        try:
            if task.task_type == BackgroundTaskType.EXPERIMENT.value:
                success = await cls._delete_experiment(
                    workspace_id=task.workspace_id,
                    experiment_uid=task.resource_id,
                )
            elif task.task_type == BackgroundTaskType.WORKSPACE.value:
                success = await cls._delete_workspace(
                    workspace_id=int(task.resource_id),
                    user_id=task.user_id,
                )
            else:
                logger.error(f"Unknown task type: {task.task_type}")
                BackgroundTaskService.mark_failed(task.id, "Unknown task type")
                return False

            if success:
                BackgroundTaskService.mark_completed(task.id)
                return True
            else:
                BackgroundTaskService.mark_failed(
                    task.id,
                    "Operation returned False (partial failure)",
                )
                return False

        except Exception as e:
            error_msg = str(e)[:500]
            logger.error(f"Task {task.id} failed: {error_msg}")
            BackgroundTaskService.mark_failed(task.id, error_msg)
            return False

    @classmethod
    async def _delete_experiment(
        cls,
        workspace_id: int,
        experiment_uid: str,
    ) -> bool:
        """Delete an experiment (S3 + local + DB record)."""
        try:
            remote_bucket_name = cls._get_remote_bucket_name()

            if RemoteStorageController.is_available():
                try:
                    RemoteSyncLockFileUtil.check_sync_lock_file(
                        str(workspace_id),
                        experiment_uid,
                        raise_error=True,
                    )
                except Exception as lock_error:
                    logger.warning(
                        "Sync lock exists for experiment "
                        f"{experiment_uid}: {lock_error}"
                    )
                    return False

            s3_success = await ExptDataWriter(
                remote_bucket_name,
                str(workspace_id),
                experiment_uid,
            ).delete_data()

            if ExperimentRecordService.is_available():
                with session_scope() as db:
                    try:
                        ExperimentRecordService.delete_record(
                            db,
                            str(workspace_id),
                            experiment_uid,
                            auto_commit=True,
                        )
                    except Exception as db_error:
                        if s3_success:
                            logger.error(
                                "DB deletion failed after " f"S3 deletion: {db_error}"
                            )
                            ExperimentRecordService.mark_as_orphaned(
                                db,
                                str(workspace_id),
                                experiment_uid,
                                error_message=(
                                    "Queued deletion: S3 deleted"
                                    ", DB error: "
                                    f"{str(db_error)[:200]}"
                                ),
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
        """Delete a workspace and all its experiments."""
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
