"""
Background Task Service - Persistent task queue.
Ensures tasks complete even if user logs out.
"""
from datetime import datetime, timezone
from typing import List, Optional

from sqlmodel import select

from studio.app.common.core.logger import AppLogger
from studio.app.common.db.database import session_scope
from studio.app.common.models.experiment import (
    BackgroundTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
)

logger = AppLogger.get_logger()

BACKGROUND_TASK_MAX_RETRIES = 3


class BackgroundTaskService:
    """Service for managing persistent background tasks."""

    @staticmethod
    def queue_experiment_deletion(
        user_id: int,
        workspace_id: int,
        experiment_uid: str,
    ) -> Optional[int]:
        """
        Queue an experiment deletion task.
        Returns immediately - deletion happens in background.

        Returns:
            Task ID if queued successfully, None on failure
        """
        try:
            with session_scope() as db:
                existing = db.execute(
                    select(BackgroundTask).where(
                        BackgroundTask.resource_id == experiment_uid,
                        BackgroundTask.task_type == BackgroundTaskType.EXPERIMENT.value,
                        BackgroundTask.status.in_(
                            [
                                BackgroundTaskStatus.QUEUED.value,
                                BackgroundTaskStatus.IN_PROGRESS.value,
                                BackgroundTaskStatus.RETRYING.value,
                            ]
                        ),
                    )
                ).first()

                if existing:
                    logger.info(
                        "Background task already exists "
                        f"for experiment {experiment_uid}"
                    )
                    return existing[0].id

                task = BackgroundTask(
                    user_id=user_id,
                    task_type=BackgroundTaskType.EXPERIMENT.value,
                    resource_id=experiment_uid,
                    workspace_id=workspace_id,
                    status=BackgroundTaskStatus.QUEUED.value,
                    max_retries=BACKGROUND_TASK_MAX_RETRIES,
                )
                db.add(task)
                db.commit()

                logger.info(
                    f"Queued background task {task.id} "
                    f"for experiment {experiment_uid}"
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

        Returns:
            Task ID if queued successfully, None on failure
        """
        try:
            with session_scope() as db:
                existing = db.execute(
                    select(BackgroundTask).where(
                        BackgroundTask.resource_id == str(workspace_id),
                        BackgroundTask.task_type == BackgroundTaskType.WORKSPACE.value,
                        BackgroundTask.status.in_(
                            [
                                BackgroundTaskStatus.QUEUED.value,
                                BackgroundTaskStatus.IN_PROGRESS.value,
                                BackgroundTaskStatus.RETRYING.value,
                            ]
                        ),
                    )
                ).first()

                if existing:
                    logger.info(
                        "Background task already exists "
                        f"for workspace {workspace_id}"
                    )
                    return existing[0].id

                task = BackgroundTask(
                    user_id=user_id,
                    task_type=BackgroundTaskType.WORKSPACE.value,
                    resource_id=str(workspace_id),
                    workspace_id=workspace_id,
                    status=BackgroundTaskStatus.QUEUED.value,
                    max_retries=BACKGROUND_TASK_MAX_RETRIES,
                )
                db.add(task)
                db.commit()

                logger.info(
                    f"Queued background task {task.id} " f"for workspace {workspace_id}"
                )
                return task.id

        except Exception as e:
            logger.error(f"Failed to queue workspace deletion: {e}")
            return None

    @staticmethod
    def get_pending_tasks(limit: int = 10) -> list:
        """
        Get pending tasks for processing.

        Returns:
            List of BackgroundTask records
        """
        try:
            with session_scope() as db:
                result = db.execute(
                    select(BackgroundTask)
                    .where(
                        BackgroundTask.status.in_(
                            [
                                BackgroundTaskStatus.QUEUED.value,
                                BackgroundTaskStatus.RETRYING.value,
                            ]
                        )
                    )
                    .order_by(BackgroundTask.created_at)
                    .limit(limit)
                ).all()
                return [row[0] for row in result] if result else []

        except Exception as e:
            logger.error(f"Failed to get pending background tasks: {e}")
            return []

    @staticmethod
    def mark_in_progress(task_id: int) -> bool:
        """Mark a task as in progress."""
        try:
            with session_scope() as db:
                task = db.get(BackgroundTask, task_id)
                if task:
                    task.status = BackgroundTaskStatus.IN_PROGRESS.value
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
                task = db.get(BackgroundTask, task_id)
                if task:
                    task.status = BackgroundTaskStatus.COMPLETED.value
                    task.completed_at = datetime.now(timezone.utc)
                    db.commit()
                    logger.info(f"Background task {task_id} completed")
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
                task = db.get(BackgroundTask, task_id)
                if task:
                    task.retry_count += 1
                    task.error_message = error_message[:1000]

                    if task.retry_count >= task.max_retries:
                        task.status = BackgroundTaskStatus.FAILED.value
                        task.completed_at = datetime.now(timezone.utc)
                        logger.error(
                            f"Background task {task_id} failed "
                            f"after {task.retry_count} attempts: "
                            f"{error_message}"
                        )
                    else:
                        task.status = BackgroundTaskStatus.RETRYING.value
                        logger.warning(
                            f"Background task {task_id} will "
                            f"retry (attempt "
                            f"{task.retry_count}/"
                            f"{task.max_retries})"
                        )

                    db.commit()
                    return True
                return False
        except Exception as e:
            logger.error(f"Failed to mark task {task_id} failed: {e}")
            return False

    @staticmethod
    def get_task_status(task_id: int) -> Optional[dict]:
        """Get the status of a background task."""
        try:
            with session_scope() as db:
                task = db.get(BackgroundTask, task_id)
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
    def get_failed_tasks_for_workspace(
        workspace_id: int,
        task_type: str,
    ) -> List[BackgroundTask]:
        """
        Get failed tasks for a specific workspace and type.
        Used to find experiments that failed deletion for retry.

        Returns:
            List of failed BackgroundTask records
        """
        try:
            with session_scope() as db:
                result = db.execute(
                    select(BackgroundTask).where(
                        BackgroundTask.workspace_id == workspace_id,
                        BackgroundTask.task_type == task_type,
                        BackgroundTask.status == BackgroundTaskStatus.FAILED.value,
                    )
                ).all()
                return [row[0] for row in result] if result else []
        except Exception as e:
            logger.error(
                "Failed to get failed tasks for " f"workspace {workspace_id}: {e}"
            )
            return []

    @staticmethod
    def cleanup_old_tasks(days_old: int = 30) -> int:
        """
        Clean up completed/failed tasks older than specified days.

        Returns:
            Number of tasks deleted
        """
        try:
            from datetime import timedelta

            from sqlalchemy import delete

            cutoff = datetime.now(timezone.utc) - timedelta(days=days_old)

            with session_scope() as db:
                result = db.execute(
                    delete(BackgroundTask).where(
                        BackgroundTask.status.in_(
                            [
                                BackgroundTaskStatus.COMPLETED.value,
                                BackgroundTaskStatus.FAILED.value,
                            ]
                        ),
                        BackgroundTask.completed_at < cutoff,
                    )
                )
                deleted_count = result.rowcount
                db.commit()

                if deleted_count > 0:
                    logger.info(
                        f"Cleaned up {deleted_count} old "
                        f"background tasks "
                        f"(older than {days_old} days)"
                    )
                return deleted_count

        except Exception as e:
            logger.error("Failed to cleanup old background tasks: " f"{e}")
            return 0
