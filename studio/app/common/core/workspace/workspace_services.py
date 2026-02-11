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

        if failed_experiments:
            failed_count = len(failed_experiments)
            total_count = len(deleted_statuses)
            logger.warning(
                f"Partial experiment deletion for '{workspace_id}': "
                f"{failed_count}/{total_count} experiments failed. "
                f"Proceeding with workspace-level cleanup."
            )

        # Workspace-level S3 prefix delete covers all experiment data
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
        except Exception as e:
            logger.error(
                f"Workspace file cleanup failed for "
                f"'{workspace_id}': {e}. "
                f"Marking workspace as deleted anyway."
            )

        ws.deleted = True
        return []

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
                await cls.delete_workspace_contents(db, ws, remote_bucket_name)
                db.commit()
                BackgroundTaskService.mark_completed(task_id)
                return True, "Workspace deleted successfully"

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

            await cls.delete_workspace_contents(db, ws, remote_bucket_name)
            db.commit()
            return True, "Workspace deleted successfully"

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
