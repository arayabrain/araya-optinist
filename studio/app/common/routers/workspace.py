import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi_pagination import LimitOffsetPage
from fastapi_pagination.ext.sqlmodel import paginate
from sqlalchemy import func
from sqlmodel import Session, or_, select

from studio.app.common import models as common_model
from studio.app.common.core.auth.auth_dependencies import (
    get_current_user,
    get_user_remote_bucket_name,
)
from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.subscription.constants import StorageSize
from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.common.core.workflow.workflow import WorkflowRunStatus
from studio.app.common.core.workspace.workspace_dependencies import (
    is_workspace_available,
    is_workspace_owner,
)
from studio.app.common.core.workspace.workspace_services import WorkspaceService
from studio.app.common.db.database import get_db
from studio.app.common.schemas.base import SortOptions
from studio.app.common.schemas.users import User
from studio.app.common.schemas.workspace import (
    Workspace,
    WorkspaceCreate,
    WorkspaceSharePostStatus,
    WorkspaceShareStatus,
    WorkspaceUpdate,
)
from studio.app.dir_path import DIRPATH

router = APIRouter(tags=["Workspace"])
logger = AppLogger.get_logger()


shared_count_subquery = (
    select(func.count())
    .select_from(common_model.WorkspacesShareUser)
    .join(
        common_model.User,
        common_model.WorkspacesShareUser.user_id == common_model.User.id,
    )
    .where(
        common_model.WorkspacesShareUser.workspace_id == common_model.Workspace.id,
        common_model.User.active.is_(True),
    )
    .correlate(common_model.Workspace)
    .scalar_subquery()
)


@router.get(
    "/workspaces",
    response_model=LimitOffsetPage[Workspace],
    description="""
- search workspaces
""",
)
def search_workspaces(
    sortOptions: SortOptions = Depends(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sa_sort_list = sortOptions.get_sa_sort_list(sa_table=common_model.Workspace)

    def workspace_transformer(items):
        list_ws = []

        # Fetch all users for these workspaces in one query for efficiency
        workspace_ids = [item.id for item in items]
        user_map = {}
        if workspace_ids:
            users_query = (
                db.query(common_model.User)
                .filter(common_model.User.id.in_([item.user_id for item in items]))
                .all()
            )
            user_map = {user.id: user for user in users_query}

        for item in items:
            # Create a Workspace object from the row data
            ws = common_model.Workspace(
                id=item.id,
                name=item.name,
                user_id=item.user_id,
                deleted=item.deleted,
                input_data_usage=item.input_data_usage,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )

            # Add the computed fields
            ws_dict = ws.__dict__
            ws_dict["shared_count"] = item.shared_count
            ws_dict["data_usage"] = item.data_usage
            ws_dict["display_number"] = item.display_number

            # Attach the user object
            ws_dict["user"] = user_map.get(item.user_id)

            workspace_dir = join_filepath([DIRPATH.OUTPUT_DIR, str(ws.id)])
            can_delete = True

            if os.path.exists(workspace_dir):
                for experiment_id in os.listdir(workspace_dir):
                    experiment_path = join_filepath([workspace_dir, experiment_id])
                    if not os.path.isdir(experiment_path):
                        continue

                    status = ExptConfigReader.read_experiment_status(
                        str(ws.id), experiment_id
                    )
                    if status is None:
                        pass
                    elif status == WorkflowRunStatus.RUNNING:
                        can_delete = False
                        break

            ws_dict["canDelete"] = can_delete

            list_ws.append(ws)

        return list_ws

    data_capacity_subq = (
        select(
            common_model.Workspace.id,
            (
                common_model.Workspace.input_data_usage
                + func.coalesce(func.sum(common_model.ExperimentRecord.data_usage), 0)
            ).label("data_usage"),
        )
        .outerjoin(
            common_model.ExperimentRecord,
            common_model.ExperimentRecord.workspace_id == common_model.Workspace.id,
        )
        .where(common_model.Workspace.deleted.is_(False))
        .group_by(common_model.Workspace.id)
        .subquery()
    )

    # Create CTE with ROW_NUMBER for display numbering
    # Calculate row numbers on ALL workspaces (including deleted) to preserve gaps
    # Order by ownership first (owned workspaces before shared), then by sort options
    all_workspaces_cte = (
        select(
            common_model.Workspace,
            shared_count_subquery.label("shared_count"),
            (data_capacity_subq.c.data_usage).label("data_usage"),
            func.row_number()
            .over(
                order_by=[
                    # First, order by ownership: owned (False) before shared (True)
                    (common_model.Workspace.user_id != current_user.id),
                    # Then apply the requested sort order
                    *(
                        sa_sort_list
                        if sa_sort_list
                        else [common_model.Workspace.created_at]
                    ),
                ]
            )
            .label("display_number"),
        )
        .outerjoin(
            data_capacity_subq, data_capacity_subq.c.id == common_model.Workspace.id
        )
        .join(
            common_model.WorkspacesShareUser,
            common_model.Workspace.id == common_model.WorkspacesShareUser.workspace_id,
            isouter=True,
        )
        .filter(
            or_(
                common_model.WorkspacesShareUser.user_id == current_user.id,
                common_model.Workspace.user_id == current_user.id,
            ),
        )
        .group_by(common_model.Workspace.id)
        .cte("all_workspaces")
    )

    # Now filter out deleted workspaces from the numbered results
    query = select(all_workspaces_cte).where(all_workspaces_cte.c.deleted.is_(False))

    data = paginate(
        db,
        query,
        transformer=workspace_transformer,
    )
    return data


@router.get(
    "/workspace/{workspace_id}",
    response_model=Workspace,
    dependencies=[Depends(is_workspace_available)],
    description="""
- get workspace by id
""",
)
def get_workspace(
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    data = (
        db.query(common_model.Workspace, shared_count_subquery.label("shared_count"))
        .outerjoin(
            common_model.WorkspacesShareUser,
            common_model.Workspace.id == common_model.WorkspacesShareUser.workspace_id,
        )
        .filter(
            common_model.Workspace.id == workspace_id,
            common_model.Workspace.deleted.is_(False),
            or_(
                common_model.WorkspacesShareUser.user_id == current_user.id,
                common_model.Workspace.user_id == current_user.id,
            ),
        )
        .first()
    )
    if not data:
        raise HTTPException(status_code=404)
    workspace, shared_count = data
    workspace.__dict__["shared_count"] = shared_count
    return Workspace.from_orm(workspace)


@router.post(
    "/workspace",
    response_model=Workspace,
    description="""
- create workspace
""",
)
def create_workspace(
    workspace: WorkspaceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    workspace = common_model.Workspace(
        **workspace.dict(), user_id=current_user.id, deleted=0
    )
    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    workspace.__dict__["user"] = current_user
    workspace.__dict__["shared_count"] = 0
    return Workspace.from_orm(workspace)


@router.put(
    "/workspace/{workspace_id}",
    response_model=Workspace,
    dependencies=[Depends(is_workspace_owner)],
    description="""
- update workspace
""",
)
def update_workspace(
    workspace_id: int,
    workspace: WorkspaceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    data = (
        db.query(common_model.Workspace, shared_count_subquery)
        .filter(
            common_model.Workspace.id == workspace_id,
            common_model.Workspace.user_id == current_user.id,
            common_model.Workspace.deleted.is_(False),
        )
        .first()
    )
    if not data:
        raise HTTPException(status_code=404)
    ws, shared_count = data
    data = workspace.dict(exclude_unset=True)
    for key, value in data.items():
        setattr(ws, key, value)
    db.commit()
    db.refresh(ws)
    ws.__dict__["user"] = current_user
    ws.__dict__["shared_count"] = shared_count
    return Workspace.from_orm(ws)


@router.delete(
    "/workspace/{workspace_id}",
    response_model=bool,
    dependencies=[Depends(is_workspace_owner)],
    description="""
- delete workspace
""",
)
async def delete_workspace(
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    remote_bucket_name: str = Depends(get_user_remote_bucket_name),
):
    await WorkspaceService.process_workspace_deletion(
        db, remote_bucket_name, workspace_id, current_user.id
    )

    return True


@router.get(
    "/workspace/share/{workspace_id}/status",
    response_model=WorkspaceShareStatus,
    dependencies=[Depends(is_workspace_available)],
    description="""
- get workspace share status
""",
)
def get_workspace_share_status(
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    workspace = (
        db.query(common_model.Workspace)
        .filter(
            common_model.Workspace.id == workspace_id,
            common_model.Workspace.user_id == current_user.id,
            common_model.Workspace.deleted.is_(False),
        )
        .first()
    )
    if not workspace:
        raise HTTPException(status_code=404)
    users = (
        db.query(common_model.User)
        .join(
            common_model.WorkspacesShareUser,
            common_model.WorkspacesShareUser.user_id == common_model.User.id,
        )
        .filter(
            common_model.WorkspacesShareUser.workspace_id == workspace_id,
            common_model.User.active.is_(True),
        )
        .all()
    )
    return WorkspaceShareStatus(users=users)


@router.post(
    "/workspace/share/{workspace_id}/status",
    response_model=bool,
    dependencies=[Depends(is_workspace_owner)],
    description="""
- update workspace share status
""",
)
def update_workspace_share_status(
    workspace_id: int,
    data: WorkspaceSharePostStatus,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    workspace = (
        db.query(common_model.Workspace)
        .filter(
            common_model.Workspace.id == workspace_id,
            common_model.Workspace.user_id == current_user.id,
            common_model.Workspace.deleted.is_(False),
        )
        .first()
    )
    if not workspace:
        raise HTTPException(status_code=404)

    (
        db.query(common_model.WorkspacesShareUser)
        .filter(common_model.WorkspacesShareUser.workspace_id == workspace_id)
        .delete(synchronize_session=False)
    )
    db.bulk_save_objects(
        common_model.WorkspacesShareUser(workspace_id=workspace_id, user_id=user_id)
        for user_id in data.user_ids
    )
    db.commit()
    return True


@router.post(
    "/workspaces/refresh-storage",
    response_model=dict,
    description="""
- refresh S3 storage usage for all workspaces
""",
)
async def refresh_all_workspaces_storage(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Refresh storage usage calculation for all workspaces.
    This will recalculate both local and S3 storage usage.
    """
    try:
        # Import both local and cloud capacity services
        import sys
        from pathlib import Path

        project_root = Path(__file__).parent.parent.parent.parent.parent
        sys.path.insert(0, str(project_root))

        # Determine if we should use S3 or local storage based on environment
        # Use shared bucket from environment, not user-specific buckets
        import os

        from studio.app.common.core.storage.remote_storage_controller import (
            RemoteStorageType,
        )
        from studio.scripts.run_sync_data_capacity_cloud import (
            CloudWorkspaceDataCapacityService,
        )

        bucket_name = None
        remote_storage_type = RemoteStorageType.get_activated_type()

        if remote_storage_type == RemoteStorageType.S3:
            # Use per-user bucket if available, otherwise fall back to shared bucket
            from studio.app.common.core.users import crud_users

            user_info = await crud_users.get_user_with_context(db, current_user.id)

            if user_info and user_info.remote_bucket_name:
                bucket_name = user_info.remote_bucket_name
            else:
                # Fallback to shared bucket
                bucket_name = os.environ.get("S3_DEFAULT_BUCKET_NAME")
                logger.warning(
                    f"User {current_user.id} has no personal bucket, "
                    f"using shared bucket: {bucket_name}"
                )

        use_s3 = bool(bucket_name)

        # Get all non-deleted workspaces that the user has access to
        from studio.app.common.core.workspace.workspace_services import WorkspaceService

        workspace_ids = WorkspaceService.get_user_accessible_workspace_ids(
            db, current_user.id
        )

        logger.info(
            f"Refreshing storage for {len(workspace_ids)} workspaces "
            f"for user {current_user.id}"
        )

        # Process each workspace
        refreshed_count = 0
        for workspace_id in workspace_ids:
            try:
                if use_s3:
                    # Use S3 storage service with user's bucket (per-user or shared)
                    service = CloudWorkspaceDataCapacityService
                    await service.sync_workspace_data_capacity_with_s3(
                        db,
                        bucket_name,
                        str(workspace_id),
                        delete_existing=False,
                    )
                else:
                    # Use local storage service - calculate actual filesystem sizes
                    from studio.app.common.core.utils.file_reader import get_folder_size

                    # Calculate input folder size
                    workspace_input_path = os.path.join(
                        DIRPATH.INPUT_DIR, str(workspace_id)
                    )
                    input_size = (
                        get_folder_size(workspace_input_path)
                        if os.path.exists(workspace_input_path)
                        else 0
                    )

                    # Calculate output folder size
                    workspace_output_path = os.path.join(
                        DIRPATH.OUTPUT_DIR, str(workspace_id)
                    )
                    output_size = (
                        get_folder_size(workspace_output_path)
                        if os.path.exists(workspace_output_path)
                        else 0
                    )

                    total_workspace_size = input_size + output_size

                    # Update workspace input_data_usage to reflect filesystem size
                    from sqlalchemy import text

                    db.execute(
                        text(
                            "UPDATE workspaces SET input_data_usage = :total_size "
                            "WHERE id = :ws_id"
                        ),
                        {"total_size": total_workspace_size, "ws_id": workspace_id},
                    )

                    # Clear stale experiment records data_usage for this workspace
                    db.execute(
                        text(
                            "UPDATE experiment_records SET data_usage = 0 "
                            "WHERE workspace_id = :ws_id"
                        ),
                        {"ws_id": workspace_id},
                    )

                refreshed_count += 1

            except Exception as e:
                logger.error(
                    f"Failed to refresh storage for workspace {workspace_id}: {e}"
                )
                continue

        db.commit()

        # After refreshing individual workspaces, update the user's total storage usage
        try:
            from studio.app.common.core.cloud.cloud_utils import (
                get_current_user_storage_usage,
            )

            # Use our unified storage calculation function with force_live=True
            total_usage = await get_current_user_storage_usage(
                current_user.id, force_live=True
            )
            logger.info(
                f"Updated user {current_user.id} total storage usage "
                f"to {total_usage} bytes ({total_usage/StorageSize.GB:.2f}GB)"
            )
        except Exception as e:
            logger.warning(f"Failed to update user total storage usage: {e}")

        logger.info(
            f"Successfully refreshed storage for {refreshed_count}/"
            f"{len(workspace_ids)} workspaces"
        )

        return {
            "success": True,
            "refreshed_workspaces": refreshed_count,
            "total_workspaces": len(workspace_ids),
            "message": (f"Refreshed storage usage for {refreshed_count} " "workspaces"),
        }

    except Exception as e:
        logger.error(f"Failed to refresh all workspaces storage: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to refresh workspace storage usage"
        )
