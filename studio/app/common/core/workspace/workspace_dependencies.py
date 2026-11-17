from typing import Optional, Union

from fastapi import Depends, HTTPException
from sqlmodel import Session, or_

from studio.app.common import models as common_model
from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.db.database import get_db, run_db_lookup
from studio.app.common.schemas.users import User

# The queries run in a worker thread (run_db_lookup), so a wait for a pooled
# connection does not block the event loop. The session keeps the connection
# for the route's own queries.


async def is_workspace_owner(
    workspace_id: Union[int, str],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    workspace = await run_db_lookup(
        _find_owned_workspace, db, workspace_id, current_user.id
    )

    if workspace is None:
        raise HTTPException(status_code=403, detail="Operation is not available")
    else:
        return True


async def is_workspace_available(
    workspace_id: Union[int, str],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> bool:
    workspace = await run_db_lookup(
        _find_available_workspace, db, workspace_id, current_user.id
    )

    if workspace is None:
        raise HTTPException(status_code=403, detail="Workspace is not available")
    else:
        return True


def _find_owned_workspace(
    db: Session, workspace_id: Union[int, str], user_id: int
) -> Optional[common_model.Workspace]:
    return (
        db.query(common_model.Workspace)
        .filter(
            common_model.Workspace.id == workspace_id,
            common_model.Workspace.deleted.is_(False),
            common_model.Workspace.user_id == user_id,
        )
        .first()
    )


def _find_available_workspace(
    db: Session, workspace_id: Union[int, str], user_id: int
) -> Optional[common_model.Workspace]:
    return (
        db.query(common_model.Workspace)
        .join(
            common_model.WorkspacesShareUser,
            common_model.WorkspacesShareUser.workspace_id == common_model.Workspace.id,
            isouter=True,
        )
        .filter(
            common_model.Workspace.id == workspace_id,
            common_model.Workspace.deleted.is_(False),
            or_(
                common_model.Workspace.user_id == user_id,
                common_model.WorkspacesShareUser.user_id == user_id,
            ),
        )
        .group_by(common_model.Workspace.id)
        .first()
    )
