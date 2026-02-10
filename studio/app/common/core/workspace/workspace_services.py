import os
import shutil
from typing import List, Tuple

from fastapi import HTTPException, status
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, or_, select

from studio.app.common import models as common_model
from studio.app.common.core.experiment.background_task_service import (
    BackgroundTaskService,
)
from studio.app.common.core.experiment.experiment_services import ExperimentService
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    StorageDirectoryType,
)
from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.common.models.experiment import ExperimentRecord
from studio.app.common.models.workspace import Workspace
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()


class WorkspaceService:
    @classmethod
    async def delete_workspace_contents(
        cls,
        db: Session,
        ws: Workspace,
        remote_bucket_name: str,
    ) -> List[str]:
        """
        Delete workspace contents. Returns failed experiment UIDs.

        Returns:
            Empty list on full success, or list of failed UIDs.
        """
        workspace_id = str(ws.id)
        logger.info(f"Deleting workspace data for workspace " f"'{workspace_id}'")

        deleted_statuses = []
        failed_experiments = []

        experiment_records = (
            db.query(ExperimentRecord)
            .filter(ExperimentRecord.workspace_id == ws.id)
            .all()
        )

        logger.info(
            f"Found {len(experiment_records)} experiment records "
            f"for workspace '{workspace_id}'"
        )

        for record in experiment_records:
            try:
                deleted_status = await ExperimentService.delete_experiment(
                    db,
                    remote_bucket_name,
                    workspace_id,
                    record.uid,
                    auto_commit=False,
                )
                deleted_statuses.append(deleted_status)
                if not deleted_status:
                    failed_experiments.append(record.uid)
            except Exception as e:
                logger.error(
                    f"Error deleting experiment {record.uid} "
                    f"in workspace {workspace_id}: {e}"
                )
                deleted_statuses.append(False)
                failed_experiments.append(record.uid)

        if len(deleted_statuses) == 0 or all(deleted_statuses):
            await cls.delete_workspace_files(
                workspace_id=workspace_id,
                remote_bucket_name=remote_bucket_name,
            )
            await cls.delete_workspace_files(
                workspace_id=workspace_id,
                remote_bucket_name=remote_bucket_name,
                is_input_dir=True,
            )
            ws.deleted = True
            return []

        failed_count = len(failed_experiments)
        total_count = len(deleted_statuses)
        logger.warning(
            f"Partial workspace deletion for '{workspace_id}': "
            f"{failed_count}/{total_count} experiments failed"
        )

        # Clean up workspace files for successfully deleted experiments
        try:
            await cls.delete_workspace_files(
                workspace_id=workspace_id,
                remote_bucket_name=remote_bucket_name,
            )
            await cls.delete_workspace_files(
                workspace_id=workspace_id,
                remote_bucket_name=remote_bucket_name,
                is_input_dir=True,
            )
        except Exception as cleanup_error:
            logger.error(f"Error cleaning up workspace files: " f"{cleanup_error}")

        return failed_experiments

    @classmethod
    async def delete_workspace_files(
        cls,
        workspace_id: str,
        remote_bucket_name,
        is_input_dir: bool = False,
    ):
        if RemoteStorageController.is_available():
            remote_storage_controller = RemoteStorageController(remote_bucket_name)
            if is_input_dir:
                await remote_storage_controller.delete_workspace(
                    workspace_id,
                    directory_type=StorageDirectoryType.INPUT,
                )
            else:
                await remote_storage_controller.delete_workspace(
                    workspace_id,
                    directory_type=StorageDirectoryType.OUTPUT,
                )

        if is_input_dir:
            directory = join_filepath([DIRPATH.INPUT_DIR, workspace_id])
        else:
            directory = join_filepath([DIRPATH.OUTPUT_DIR, workspace_id])
        try:
            if os.path.exists(directory):
                shutil.rmtree(directory)
                logger.info(f"Deleted directory: {directory}")
            else:
                logger.warning(f"'{directory}' already deleted " f"or never existed")
        except Exception as e:
            logger.error(
                f"Failed to delete directory '{directory}': {e}",
                exc_info=True,
            )

    @classmethod
    async def initiate_workspace_deletion(
        cls,
        db: Session,
        remote_bucket_name: str,
        workspace_id: str,
        user_id: str,
    ) -> Tuple[bool, str]:
        """
        API entry point for workspace deletion.
        Creates a background task and processes deletion.

        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            # Check for active task before acquiring row lock
            if BackgroundTaskService.has_active_workspace_task(int(workspace_id)):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Workspace deletion already in progress",
                )

            stmt = (
                select(Workspace)
                .where(
                    Workspace.id == workspace_id,
                    Workspace.user_id == user_id,
                    Workspace.deleted.is_(False),
                )
                .with_for_update(nowait=True)
            )

            result = db.execute(stmt)
            ws = result.scalar_one_or_none()

            if not ws:
                raise HTTPException(
                    status_code=404,
                    detail="Workspace not found or already deleted",
                )

            # Create background task as "in progress" marker
            task_id = BackgroundTaskService.queue_workspace_deletion(
                user_id=int(user_id),
                workspace_id=int(workspace_id),
            )
            BackgroundTaskService.mark_in_progress(task_id)
            db.commit()

            try:
                failed_uids = await cls.delete_workspace_contents(
                    db, ws, remote_bucket_name
                )

                if not failed_uids:
                    db.commit()
                    BackgroundTaskService.mark_completed(task_id)
                    return True, "Workspace deleted successfully"

                # Partial failure: queue failed experiments
                for uid in failed_uids:
                    BackgroundTaskService.queue_experiment_deletion(
                        user_id=ws.user_id,
                        workspace_id=ws.id,
                        experiment_uid=uid,
                    )

                failed_count = len(failed_uids)
                BackgroundTaskService.mark_failed(
                    task_id,
                    f"{failed_count} experiments failed",
                )
                db.commit()
                raise HTTPException(
                    status_code=status.HTTP_207_MULTI_STATUS,
                    detail=(
                        f"Partial deletion: {failed_count} "
                        f"experiments failed to delete. "
                        f"Queued for background retry."
                    ),
                )

            except HTTPException:
                raise
            except Exception as e:
                BackgroundTaskService.mark_failed(task_id, str(e)[:500])
                raise

        except OperationalError as e:
            db.rollback()
            if (
                "could not obtain lock" in str(e).lower()
                or "lock wait" in str(e).lower()
            ):
                logger.warning(
                    "Workspace %s is being modified by " "another request",
                    workspace_id,
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=("Workspace is being modified by " "another request"),
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=("Database error during workspace " f"deletion: {workspace_id}"),
            )
        except HTTPException:
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            logger.error(
                "Error deleting or updating workspace %s: %s",
                workspace_id,
                e,
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(f"Failed to delete or update " f"workspace {workspace_id}."),
            )

    @classmethod
    async def execute_workspace_deletion(
        cls,
        db: Session,
        remote_bucket_name: str,
        workspace_id: int,
        user_id: int,
    ) -> Tuple[bool, str]:
        """
        Worker entry point for workspace deletion.
        Task already exists and is marked in_progress by worker.

        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            stmt = (
                select(Workspace)
                .where(
                    Workspace.id == workspace_id,
                    Workspace.user_id == user_id,
                    Workspace.deleted.is_(False),
                )
                .with_for_update(nowait=True)
            )

            result = db.execute(stmt)
            ws = result.scalar_one_or_none()

            if not ws:
                return True, "Already deleted"

            failed_uids = await cls.delete_workspace_contents(
                db, ws, remote_bucket_name
            )

            if not failed_uids:
                db.commit()
                return True, "Workspace deleted successfully"

            # Queue failed experiments for background retry
            for uid in failed_uids:
                BackgroundTaskService.queue_experiment_deletion(
                    user_id=ws.user_id,
                    workspace_id=ws.id,
                    experiment_uid=uid,
                )

            db.commit()
            return (
                False,
                f"{len(failed_uids)} experiments failed",
            )

        except OperationalError as e:
            db.rollback()
            if "could not obtain lock" in str(e).lower():
                return (
                    False,
                    "Workspace is being modified by " "another request",
                )
            return False, f"Database error: {e}"
        except Exception as e:
            db.rollback()
            logger.error(
                f"Worker workspace deletion failed: {e}",
                exc_info=True,
            )
            return False, f"Error: {e}"

    @staticmethod
    def get_user_accessible_workspace_ids(db: Session, user_id: int) -> List[int]:
        """
        Get all workspace IDs that a user has access to
        (owned or shared).
        """
        try:
            workspaces_query = (
                select(common_model.Workspace.id)
                .join(
                    common_model.WorkspacesShareUser,
                    common_model.Workspace.id
                    == common_model.WorkspacesShareUser.workspace_id,
                    isouter=True,
                )
                .filter(
                    common_model.Workspace.deleted.is_(False),
                    or_(
                        common_model.WorkspacesShareUser.user_id == user_id,
                        common_model.Workspace.user_id == user_id,
                    ),
                )
            )
            workspace_ids = db.execute(workspaces_query).scalars().all()

            return list(workspace_ids)

        except Exception as e:
            logger.error(
                "Failed to get accessible workspace IDs " f"for user {user_id}: {e}"
            )
            return []
