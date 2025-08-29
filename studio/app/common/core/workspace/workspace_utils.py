"""
Shared workspace utility functions to eliminate code duplication.
"""
from typing import List

from sqlmodel import Session, or_, select

from studio.app.common import models as common_model
from studio.app.common.core.logger import AppLogger

logger = AppLogger.get_logger()


def get_user_accessible_workspace_ids(db: Session, user_id: int) -> List[int]:
    """
    Get all workspace IDs that a user has access to (owned or shared).

    This function centralizes the workspace access query logic that was
    duplicated across cloud_utils.py, workspace.py router, and s3_storage_monitor.py.

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

        logger.debug(f"User {user_id} has access to {len(workspace_ids)} workspaces")
        return list(workspace_ids)

    except Exception as e:
        logger.error(f"Failed to get accessible workspace IDs for user {user_id}: {e}")
        return []
