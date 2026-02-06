"""
Deletion Task Service - Persistent deletion queue.
Ensures deletions complete even if user logs out.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import select

from studio.app.common.core.logger import AppLogger
from studio.app.common.db.database import session_scope
from studio.app.common.models.experiment import (
    DeletionTask,
    DeletionTaskStatus,
    DeletionTaskType,
)

logger = AppLogger.get_logger()

DELETION_TASK_MAX_RETRIES = 3


class DeletionTaskService:
    """Service for managing persistent deletion tasks."""

    @staticmethod
    def queue_experiment_deletion(
        user_id: int,
        workspace_id: int,
        experiment_uid: str,
    ) -> Optional[int]:
        """
        Queue an experiment deletion task.
        Returns immediately - deletion happens in background.

        Args:
            user_id: User initiating deletion
            workspace_id: Workspace containing the experiment
            experiment_uid: Unique ID of experiment to delete

        Returns:
            Task ID if queued successfully, None on failure
        """
        try:
            with session_scope() as db:
                # Check for existing pending/in-progress task
                existing = db.execute(
                    select(DeletionTask).where(
                        DeletionTask.resource_id == experiment_uid,
                        DeletionTask.task_type == DeletionTaskType.EXPERIMENT.value,
                        DeletionTask.status.in_(
                            [
                                DeletionTaskStatus.QUEUED.value,
                                DeletionTaskStatus.IN_PROGRESS.value,
                                DeletionTaskStatus.RETRYING.value,
                            ]
                        ),
                    )
                ).first()

                if existing:
                    logger.info(
                        f"Deletion task already exists for experiment {experiment_uid}"
                    )
                    return existing[0].id

                task = DeletionTask(
                    user_id=user_id,
                    task_type=DeletionTaskType.EXPERIMENT.value,
                    resource_id=experiment_uid,
                    workspace_id=workspace_id,
                    status=DeletionTaskStatus.QUEUED.value,
                    max_retries=DELETION_TASK_MAX_RETRIES,
                )
                db.add(task)
                db.commit()

                logger.info(
                    f"Queued deletion task {task.id} for experiment {experiment_uid}"
                )
                return task.id

        except Exception as e:
            logger.error(f"Failed to queue experiment deletion: {e}")
            return None

    @staticmethod
    def queue_workspace_deletion(
        user_id: int,
        workspace_id: int,
    ) -> Optional[int]:
        """
        Queue a workspace deletion task.
        Returns immediately - deletion happens in background.

        Args:
            user_id: User initiating deletion
            workspace_id: Workspace to delete

        Returns:
            Task ID if queued successfully, None on failure
        """
        try:
            with session_scope() as db:
                # Check for existing pending/in-progress task
                existing = db.execute(
                    select(DeletionTask).where(
                        DeletionTask.resource_id == str(workspace_id),
                        DeletionTask.task_type == DeletionTaskType.WORKSPACE.value,
                        DeletionTask.status.in_(
                            [
                                DeletionTaskStatus.QUEUED.value,
                                DeletionTaskStatus.IN_PROGRESS.value,
                                DeletionTaskStatus.RETRYING.value,
                            ]
                        ),
                    )
                ).first()

                if existing:
                    logger.info(
                        f"Deletion task already exists for workspace {workspace_id}"
                    )
                    return existing[0].id

                task = DeletionTask(
                    user_id=user_id,
                    task_type=DeletionTaskType.WORKSPACE.value,
                    resource_id=str(workspace_id),
                    workspace_id=workspace_id,
                    status=DeletionTaskStatus.QUEUED.value,
                    max_retries=DELETION_TASK_MAX_RETRIES,
                )
                db.add(task)
                db.commit()

                logger.info(
                    f"Queued deletion task {task.id} for workspace {workspace_id}"
                )
                return task.id

        except Exception as e:
            logger.error(f"Failed to queue workspace deletion: {e}")
            return None

    @staticmethod
    def get_pending_tasks(limit: int = 10) -> list:
        """
        Get pending deletion tasks for processing.

        Args:
            limit: Maximum number of tasks to return

        Returns:
            List of DeletionTask records
        """
        try:
            with session_scope() as db:
                result = db.execute(
                    select(DeletionTask)
                    .where(
                        DeletionTask.status.in_(
                            [
                                DeletionTaskStatus.QUEUED.value,
                                DeletionTaskStatus.RETRYING.value,
                            ]
                        )
                    )
                    .order_by(DeletionTask.created_at)
                    .limit(limit)
                ).all()
                return [row[0] for row in result] if result else []

        except Exception as e:
            logger.error(f"Failed to get pending deletion tasks: {e}")
            return []

    @staticmethod
    def mark_in_progress(task_id: int) -> bool:
        """Mark a task as in progress."""
        try:
            with session_scope() as db:
                task = db.get(DeletionTask, task_id)
                if task:
                    task.status = DeletionTaskStatus.IN_PROGRESS.value
                    task.started_at = datetime.now(timezone.utc)
                    db.commit()
                    return True
                return False
        except Exception as e:
            logger.error(f"Failed to mark task {task_id} in progress: {e}")
            return False

    @staticmethod
    def mark_completed(task_id: int) -> bool:
        """Mark a task as completed."""
        try:
            with session_scope() as db:
                task = db.get(DeletionTask, task_id)
                if task:
                    task.status = DeletionTaskStatus.COMPLETED.value
                    task.completed_at = datetime.now(timezone.utc)
                    db.commit()
                    logger.info(f"Deletion task {task_id} completed successfully")
                    return True
                return False
        except Exception as e:
            logger.error(f"Failed to mark task {task_id} completed: {e}")
            return False

    @staticmethod
    def mark_failed(task_id: int, error_message: str) -> bool:
        """
        Mark a task as failed or queue for retry.
        Automatically retries up to max_retries times.
        """
        try:
            with session_scope() as db:
                task = db.get(DeletionTask, task_id)
                if task:
                    task.retry_count += 1
                    task.error_message = error_message[:1000]  # Truncate

                    if task.retry_count >= task.max_retries:
                        task.status = DeletionTaskStatus.FAILED.value
                        task.completed_at = datetime.now(timezone.utc)
                        logger.error(
                            f"Deletion task {task_id} failed after "
                            f"{task.retry_count} attempts: {error_message}"
                        )
                    else:
                        task.status = DeletionTaskStatus.RETRYING.value
                        logger.warning(
                            f"Deletion task {task_id} will retry "
                            f"(attempt {task.retry_count}/{task.max_retries})"
                        )

                    db.commit()
                    return True
                return False
        except Exception as e:
            logger.error(f"Failed to mark task {task_id} failed: {e}")
            return False

    @staticmethod
    def get_task_status(task_id: int) -> Optional[dict]:
        """Get the status of a deletion task."""
        try:
            with session_scope() as db:
                task = db.get(DeletionTask, task_id)
                if task:
                    return {
                        "id": task.id,
                        "status": task.status,
                        "task_type": task.task_type,
                        "resource_id": task.resource_id,
                        "retry_count": task.retry_count,
                        "error_message": task.error_message,
                        "created_at": task.created_at,
                        "completed_at": task.completed_at,
                    }
                return None
        except Exception as e:
            logger.error(f"Failed to get task {task_id} status: {e}")
            return None

    @staticmethod
    def cleanup_old_tasks(days_old: int = 30) -> int:
        """
        Clean up completed/failed tasks older than specified days.

        Args:
            days_old: Delete tasks older than this

        Returns:
            Number of tasks deleted
        """
        try:
            from datetime import timedelta

            from sqlalchemy import delete

            cutoff = datetime.now(timezone.utc) - timedelta(days=days_old)

            with session_scope() as db:
                result = db.execute(
                    delete(DeletionTask).where(
                        DeletionTask.status.in_(
                            [
                                DeletionTaskStatus.COMPLETED.value,
                                DeletionTaskStatus.FAILED.value,
                            ]
                        ),
                        DeletionTask.completed_at < cutoff,
                    )
                )
                deleted_count = result.rowcount
                db.commit()

                if deleted_count > 0:
                    logger.info(
                        f"Cleaned up {deleted_count} old deletion tasks "
                        f"(older than {days_old} days)"
                    )
                return deleted_count

        except Exception as e:
            logger.error(f"Failed to cleanup old deletion tasks: {e}")
            return 0
