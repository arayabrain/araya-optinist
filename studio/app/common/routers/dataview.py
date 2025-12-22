import os
from typing import List, Sequence

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from fastapi_pagination.ext.sqlmodel import paginate
from sqlalchemy.sql import Select
from sqlmodel import Session, select

from studio.app.common import models
from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.dataview.dataview_services import DataviewService
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.storage.s3_storage_controller import S3StorageController
from studio.app.common.db.database import get_db
from studio.app.common.routers.workflow import reproduce_experiment
from studio.app.common.schemas.base import SortDirection, SortOptions
from studio.app.common.schemas.dataview import (
    DataviewRecord,
    DataviewRecordHeader,
    DataviewRecordSearchOptions,
    LocalSyncStatus,
    PageWithHeader,
    PublishFlags,
    PublishStatus,
)
from studio.app.common.schemas.users import User
from studio.app.common.schemas.workflow import WorkflowWithResults
from studio.app.dir_path import DIRPATH

router = APIRouter(tags=["Dataview"], prefix="/api/dataview")
public_router = APIRouter(tags=["Dataview"], prefix="/api/public/dataview")

logger = AppLogger.get_logger()


RECORDS_SORT_MAPPING = {
    "user_name": models.User.name,
    "workspace_name": models.Workspace.name,
    "timestamp": models.ExperimentRecord.analyzed_at,
}


def records_pagenate_transformer(items: Sequence) -> Sequence:
    records = []

    for idx, item in enumerate(items):
        record = DataviewRecord.from_orm(item)

        # Adjusting response fields
        record.owner = record.workspace.user
        record.workspace.user = None  # Not used

        records.append(record)

    return records


def get_records_common_query(sortOptions: SortOptions) -> Select:
    query = (
        select(models.ExperimentRecord)
        .join(
            models.Workspace,
            models.Workspace.id == models.ExperimentRecord.workspace_id,
        )
        .join(
            models.User,
            models.User.id == models.Workspace.user_id,
        )
        .filter(
            models.Workspace.deleted.is_(False),
            models.User.active.is_(True),
            models.ExperimentRecord.success.is_(True),
        )
    )

    sa_sort_list = sortOptions.get_sa_sort_list(
        sa_table=models.ExperimentRecord,
        mapping=RECORDS_SORT_MAPPING,
        default_sort=["analyzed_at", SortDirection.desc],
    )
    query = query.order_by(*sa_sort_list)

    return query


def get_records_filtered_query(
    query: Select, options: DataviewRecordSearchOptions
) -> Select:
    if options.uid:
        query = query.filter(
            models.ExperimentRecord.uid.like("%{0}%".format(options.uid))
        )

    if options.name:
        query = query.filter(
            models.ExperimentRecord.name.like("%{0}%".format(options.name))
        )

    if options.user_name:
        query = query.filter(models.User.name.like("%{0}%".format(options.user_name)))

    if options.workspace_id:
        query = query.filter(models.Workspace.id == int(options.workspace_id))

    if options.workspace_name:
        query = query.filter(
            models.Workspace.name.like("%{0}%".format(options.workspace_name))
        )

    if options.publish_status is not None:
        query = query.filter(
            models.ExperimentRecord.publish_status == options.publish_status
        )

    return query


@public_router.get(
    "",
    response_model=PageWithHeader[DataviewRecord],
    description="""
- Search and respond to data for display in Public Dataview
""",
)
async def public_search_dataview_records(
    db: Session = Depends(get_db),
    options: DataviewRecordSearchOptions = Depends(),
    sortOptions: SortOptions = Depends(),
):
    query = get_records_common_query(sortOptions).filter(
        models.ExperimentRecord.publish_status == PublishStatus.on.value,
    )
    query = get_records_filtered_query(query, options)

    data: PageWithHeader = paginate(
        session=db,
        query=query,
        transformer=records_pagenate_transformer,
    )

    return data


@router.get(
    "",
    response_model=PageWithHeader[DataviewRecord],
    description="""
- Search and respond to data for display in Dataview
""",
)
async def search_dataview_records(
    db: Session = Depends(get_db),
    options: DataviewRecordSearchOptions = Depends(),
    sortOptions: SortOptions = Depends(),
    current_user: User = Depends(get_current_user),
):
    query = get_records_common_query(sortOptions).filter(
        models.User.id == current_user.id,
    )
    query = get_records_filtered_query(query, options)

    if options.workspace_id:
        workspace_record = (
            db.query(models.Workspace)
            .filter(
                models.Workspace.id == options.workspace_id,
                models.Workspace.user_id == current_user.id,
                models.Workspace.deleted.is_(False),
            )
            .first()
        )

        record_header = (
            DataviewRecordHeader(
                workspace_id=workspace_record.id, workspace_name=workspace_record.name
            )
            if workspace_record
            else DataviewRecordHeader()
        )
    else:
        record_header = DataviewRecordHeader()

    data: PageWithHeader = paginate(
        session=db,
        query=query,
        transformer=records_pagenate_transformer,
        additional_data={"header": record_header},
    )

    return data


@public_router.get(
    "/workflow/reproduce/{workspace_id}/{unique_id}",
    response_model=WorkflowWithResults,
    description="""
- Public access wrapper for `GET /workflow/reproduce`
- Returns 202 if experiment is published but not yet synced
""",
)
async def public_reproduce_experiment(
    workspace_id: str,
    unique_id: str,
    db: Session = Depends(get_db),
):
    # Check target record accessibility
    record = DataviewService.find_published_dataview_record(
        db, int(workspace_id), unique_id
    )

    if not record:
        raise HTTPException(status_code=404)

    # Ensure experiment is available on local EBS (download from S3 if needed)
    experiment_path = os.path.join(DIRPATH.OUTPUT_DIR, workspace_id, unique_id)
    if not os.path.exists(experiment_path):
        s3_bucket = os.environ.get("S3_DEFAULT_BUCKET_NAME")
        if s3_bucket:
            logger.info(
                f"Downloading published experiment {workspace_id}/{unique_id} from S3"
            )
            s3_controller = S3StorageController(s3_bucket)
            available = await s3_controller.download_experiment(workspace_id, unique_id)

            if not available:
                logger.error(
                    f"Failed to download experiment {workspace_id}/{unique_id} from S3"
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "download_error",
                        "message": "Failed to load experiment data, "
                        "please try again later",
                    },
                )

    # Check local sync status
    if hasattr(record, "local_sync_status"):
        if record.local_sync_status == LocalSyncStatus.pending.value:
            # Experiment is published but not yet synced to this instance
            logger.info(
                f"Experiment {workspace_id}/{unique_id} is pending sync, "
                f"returning 202"
            )
            return JSONResponse(
                status_code=202,
                content={
                    "status": "pending_sync",
                    "message": (
                        "Publishing in progress, check back in a few minutes. "
                        "Experiments are typically available within 5 minutes."
                    ),
                    "retry_after": 300,  # Suggest retry after 5 minutes
                },
                headers={"Retry-After": "300"},
            )

        elif record.local_sync_status == LocalSyncStatus.error.value:
            # Sync failed, return error
            logger.error(f"Experiment {workspace_id}/{unique_id} has sync error")
            return JSONResponse(
                status_code=503,
                content={
                    "status": "sync_error",
                    "message": "Experiment sync failed, please try again later",
                },
            )

    # Data is synced and available locally
    return await reproduce_experiment(workspace_id, unique_id)


@router.post(
    "/publish/{id}/{flag}",
    response_model=bool,
    description="""
- Publishing Dataview records with optimistic locking
""",
)
async def publish_dataview_records(
    id: int,
    flag: PublishFlags,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from sqlalchemy import update

    max_retries = 3
    for attempt in range(max_retries):
        try:
            record = DataviewService.find_user_owned_dataview_record(
                db, id, current_user.id
            )

            if not record:
                raise HTTPException(status_code=404)

            # Store current version for optimistic locking
            current_version = record.version

            # Update publish status
            new_publish_status = int(flag == PublishFlags.on)

            # Check if status is actually changing
            if record.publish_status == new_publish_status:
                logger.info(
                    f"Experiment {record.id} already "
                    f"has publish_status={new_publish_status}, no change needed"
                )
                return True

            # Determine new sync status
            new_sync_status = (
                LocalSyncStatus.pending.value
                if flag == PublishFlags.on
                else LocalSyncStatus.synced.value
            )

            # Use SQLAlchemy's update() with WHERE clause for optimistic locking
            # This is the proper ORM way to implement optimistic locking
            stmt = (
                update(models.ExperimentRecord)
                .where(models.ExperimentRecord.id == record.id)
                .where(models.ExperimentRecord.version == current_version)
                .values(
                    publish_status=new_publish_status,
                    local_sync_status=new_sync_status,
                    version=models.ExperimentRecord.version + 1,
                )
            )

            result = db.execute(stmt)
            db.commit()

            # Check if update actually occurred (rowcount = 0 means version conflict)
            if result.rowcount == 0:
                # Version mismatch - concurrent modification detected
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Optimistic lock conflict for experiment {id}, "
                        f"retrying (attempt {attempt + 1}/{max_retries})"
                    )
                    continue
                else:
                    raise HTTPException(
                        status_code=409,
                        detail="Concurrent modification detected. Please try again.",
                    )

            # Success - log the action
            action = "Published" if flag == PublishFlags.on else "Unpublished"
            logger.info(
                f"{action} experiment {record.id}, " f"sync_status={new_sync_status}"
            )
            return True

        except HTTPException:
            # Re-raise HTTP exceptions (404, 409)
            raise
        except Exception as e:
            # Unexpected error
            db.rollback()
            logger.error(f"Error publishing experiment {id}: {e}", exc_info=True)
            if attempt < max_retries - 1:
                logger.warning(f"Retrying (attempt {attempt + 1}/{max_retries})")
                continue
            else:
                raise HTTPException(
                    status_code=500, detail=f"Failed to publish experiment: {str(e)}"
                )

    # Should never reach here
    raise HTTPException(
        status_code=500, detail="Failed to publish experiment after multiple retries"
    )


@router.post(
    "/multiple/publish/{flag}",
    response_model=bool,
    description="""
- Publishing Dataview records in bulk
""",
)
def multiple_publish_dataview_records(
    ids: List[int],
    flag: PublishFlags,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    DataviewService.multiple_publish_dataview_records(db, current_user.id, ids, flag)

    return True
