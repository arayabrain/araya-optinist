import os
import shutil
from datetime import datetime, timezone
from typing import List, Tuple

from fastapi import HTTPException, status
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, or_, select

from studio.app.common import models as common_model
from studio.app.common.core.experiment.experiment_services import ExperimentService
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    StorageDirectoryType,
)
from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.common.models.experiment import ExperimentRecord
from studio.app.common.models.workspace import Workspace, WorkspaceStatus
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()


class WorkspaceService:
    @classmethod
    async def delete_workspace_contents(
        cls,
        db: Session,
        ws: Workspace,
        remote_bucket_name: str,
    ):
        """
        Delete workspace contents with partial deletion recovery.
        Continues deleting remaining experiments even if some fail.
        """
        workspace_id = str(ws.id)
        logger.info(f"Deleting workspace data for workspace '{workspace_id}'")

        deleted_statuses = []
        failed_experiments = []

        # Query ExperimentRecords from database (source of truth)
        experiment_records = (
            db.query(ExperimentRecord)
            .filter(ExperimentRecord.workspace_id == ws.id)
            .all()
        )

        logger.info(
            f"Found {len(experiment_records)} experiment records "
            f"for workspace '{workspace_id}'"
        )

        # Delete each experiment (S3 + local + DB record)
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
                    f"Error deleting experiment {record.uid} in workspace "
                    f"{workspace_id}: {e}"
                )
                deleted_statuses.append(False)
                failed_experiments.append(record.uid)

        # Check if all deletions succeeded (or there were no experiments)
        if len(deleted_statuses) == 0 or all(deleted_statuses):
            # Delete the workspace directory itself (cleanup any remaining files)
            await cls.delete_workspace_files(
                workspace_id=workspace_id, remote_bucket_name=remote_bucket_name
            )

            # Delete input directory
            await cls.delete_workspace_files(
                workspace_id=workspace_id,
                remote_bucket_name=remote_bucket_name,
                is_input_dir=True,
            )

            # Soft delete the workspace
            ws.deleted = True
            ws.status = WorkspaceStatus.DELETED
        else:
            failed_count = len(failed_experiments)
            total_count = len(deleted_statuses)

            logger.warning(
                f"Partial workspace deletion for '{workspace_id}': "
                f"{failed_count}/{total_count} experiments failed"
            )

            # Store failed experiment UIDs for retry
            ws.status = WorkspaceStatus.PARTIAL_DELETE
            ws.failed_experiment_uids = ",".join(failed_experiments)
            ws.deletion_error = (
                f"Partial deletion: {failed_count}/{total_count} experiments failed. "
                f"Failed UIDs: {', '.join(failed_experiments[:5])}"
                + ("..." if len(failed_experiments) > 5 else "")
            )

            # Still try to delete workspace files for successfully deleted experiments
            # This cleans up as much as possible
            try:
                await cls.delete_workspace_files(
                    workspace_id=workspace_id, remote_bucket_name=remote_bucket_name
                )
                await cls.delete_workspace_files(
                    workspace_id=workspace_id,
                    remote_bucket_name=remote_bucket_name,
                    is_input_dir=True,
                )
            except Exception as cleanup_error:
                logger.error(f"Error cleaning up workspace files: {cleanup_error}")

            # Raise exception to notify caller of partial failure
            raise HTTPException(
                status_code=status.HTTP_207_MULTI_STATUS,
                detail=f"Partial deletion: {failed_count}/{total_count} experiments "
                f"failed to delete. Workspace marked for retry.",
            )

    @classmethod
    async def delete_workspace_files(
        cls, workspace_id: str, remote_bucket_name, is_input_dir: bool = False
    ):
        if RemoteStorageController.is_available():
            # delete remote data
            remote_storage_controller = RemoteStorageController(remote_bucket_name)
            if is_input_dir:
                await remote_storage_controller.delete_workspace(
                    workspace_id,
                    directory_type=StorageDirectoryType.INPUT,
                )
            else:
                await remote_storage_controller.delete_workspace(
                    workspace_id, directory_type=StorageDirectoryType.OUTPUT
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
                logger.warning(f"'{directory}' already deleted or never existed")
        except Exception as e:
            logger.error(
                f"Failed to delete directory '{directory}': {e}",
                exc_info=True,
            )

    @classmethod
    async def process_workspace_deletion(
        cls, db: Session, remote_bucket_name: str, workspace_id: str, user_id: str
    ) -> Tuple[bool, str]:
        """
        Delete workspace with row-level locking for concurrent request protection.

        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            # Acquire exclusive lock on workspace row using with_for_update
            # nowait=True causes immediate failure if row is already locked
            stmt = (
                select(Workspace)
                .where(
                    Workspace.id == workspace_id,
                    Workspace.user_id == user_id,
                    Workspace.deleted.is_(False),
                    Workspace.status != WorkspaceStatus.DELETING,
                )
                .with_for_update(nowait=True)
            )

            result = db.execute(stmt)
            ws = result.scalar_one_or_none()

            if not ws:
                raise HTTPException(
                    status_code=404,
                    detail="Workspace not found or already being deleted",
                )

            # Mark workspace as DELETING to prevent new operations
            ws.status = WorkspaceStatus.DELETING
            db.commit()

            try:
                # Delete workspace storage files
                await cls.delete_workspace_contents(db, ws, remote_bucket_name)

                # Mark as fully deleted with timestamp
                ws.deleted_at = datetime.now(timezone.utc)

                # Commit all DB changes
                db.commit()
                return True, "Workspace deleted successfully"

            except Exception as e:
                # Rollback workspace status if deletion fails
                ws.status = WorkspaceStatus.ACTIVE
                db.commit()
                raise e

        except OperationalError as e:
            db.rollback()
            # Check if this is a lock acquisition failure
            if (
                "could not obtain lock" in str(e).lower()
                or "lock wait" in str(e).lower()
            ):
                logger.warning(
                    "Workspace %s is being modified by another request",
                    workspace_id,
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Workspace is being modified by another request",
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Database error during workspace deletion: {workspace_id}",
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
                detail=f"Failed to delete or update workspace {workspace_id}.",
            )

    @classmethod
    async def retry_partial_deletion(
        cls,
        db: Session,
        remote_bucket_name: str,
        workspace_id: str,
        user_id: str,
    ) -> Tuple[bool, str]:
        """
        Retry deletion for a workspace that had partial deletion failure.

        Args:
            db: Database session
            remote_bucket_name: S3 bucket name
            workspace_id: Workspace to retry deletion
            user_id: User who owns the workspace

        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            # Get workspace with partial delete status
            stmt = (
                select(Workspace)
                .where(
                    Workspace.id == workspace_id,
                    Workspace.user_id == user_id,
                    Workspace.status == WorkspaceStatus.PARTIAL_DELETE,
                )
                .with_for_update(nowait=True)
            )

            result = db.execute(stmt)
            ws = result.scalar_one_or_none()

            if not ws:
                return False, "Workspace not found or not in partial deletion state"

            # Get failed experiment UIDs
            failed_uids = []
            if ws.failed_experiment_uids:
                failed_uids = [
                    uid.strip()
                    for uid in ws.failed_experiment_uids.split(",")
                    if uid.strip()
                ]

            if not failed_uids:
                # No failed experiments, mark as deleted
                ws.deleted = True
                ws.status = WorkspaceStatus.DELETED
                ws.deletion_error = None
                ws.failed_experiment_uids = None
                db.commit()
                return True, "Workspace deletion completed (no failed experiments)"

            # Mark as deleting for retry
            ws.status = WorkspaceStatus.DELETING
            db.commit()

            # Retry deletion for failed experiments
            still_failed = []
            for uid in failed_uids:
                try:
                    success = await ExperimentService.delete_experiment(
                        db, remote_bucket_name, str(ws.id), uid, auto_commit=False
                    )
                    if not success:
                        still_failed.append(uid)
                except Exception as e:
                    logger.error(f"Retry deletion failed for {uid}: {e}")
                    still_failed.append(uid)

            if still_failed:
                # Still have failures
                ws.status = WorkspaceStatus.PARTIAL_DELETE
                ws.failed_experiment_uids = ",".join(still_failed)
                ws.deletion_error = (
                    f"Retry partial deletion: {len(still_failed)} experiments "
                    f"still failing"
                )
                db.commit()
                return False, f"Retry failed for {len(still_failed)} experiments"
            else:
                # All experiments deleted, complete workspace deletion
                await cls.delete_workspace_files(
                    workspace_id=str(ws.id), remote_bucket_name=remote_bucket_name
                )
                await cls.delete_workspace_files(
                    workspace_id=str(ws.id),
                    remote_bucket_name=remote_bucket_name,
                    is_input_dir=True,
                )

                ws.deleted = True
                ws.status = WorkspaceStatus.DELETED
                ws.deletion_error = None
                ws.failed_experiment_uids = None
                ws.deleted_at = datetime.now(timezone.utc)
                db.commit()
                return True, "Workspace deletion completed successfully"

        except OperationalError as e:
            db.rollback()
            if "could not obtain lock" in str(e).lower():
                return False, "Workspace is being modified by another request"
            return False, f"Database error: {e}"
        except Exception as e:
            db.rollback()
            logger.error(f"Retry partial deletion failed: {e}", exc_info=True)
            return False, f"Error: {e}"

    @staticmethod
    def get_user_accessible_workspace_ids(db: Session, user_id: int) -> List[int]:
        """
        Get all workspace IDs that a user has access to (owned or shared).

        This function centralizes the workspace access query logic that was
        duplicated across cloud_utils.py, workspace.py router,
        and s3_storage_monitor.py.

        Args:
            db: Database session
            user_id: User ID to get accessible workspaces for

        Returns:
            List of workspace IDs the user can access
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
                f"Failed to get accessible workspace IDs for user {user_id}: {e}"
            )
            return []
