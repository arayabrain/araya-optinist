import os
from typing import List, Optional, Sequence, Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from fastapi_pagination.ext.sqlmodel import paginate
from sqlalchemy.sql import Select
from sqlmodel import Session, select

from studio.app.common import models
from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.dataview.dataview_services import (
    DataviewService,
    PublishValidator,
)
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    RemoteStorageSimpleReader,
    RemoteStorageType,
)
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
    # Also try to sync if status is pending/error - the local data might be incomplete
    experiment_path = os.path.join(DIRPATH.OUTPUT_DIR, workspace_id, unique_id)
    needs_sync = not os.path.exists(experiment_path)
    if not needs_sync and hasattr(record, "local_sync_status"):
        # Also sync if status indicates data might be incomplete
        needs_sync = record.local_sync_status in [
            LocalSyncStatus.pending.value,
            LocalSyncStatus.error.value,
        ]

    if needs_sync:
        # Get owner's bucket from workspace
        workspace = (
            db.query(models.Workspace)
            .filter(models.Workspace.id == int(workspace_id))
            .first()
        )
        owner_bucket = None
        if workspace and workspace.user:
            owner_bucket = getattr(workspace.user, "remote_bucket_name", None)
        remote_bucket_name = owner_bucket or os.environ.get("S3_DEFAULT_BUCKET_NAME")

        async with RemoteStorageSimpleReader(
            remote_bucket_name
        ) as remote_storage_controller:
            logger.info(
                f"Downloading published experiment {workspace_id}/{unique_id} "
                f"from remote bucket {remote_bucket_name}"
            )
            available = await remote_storage_controller.download_experiment(
                workspace_id,
                unique_id,
            )

            if not available:
                logger.error(
                    f"Failed to download experiment {workspace_id}/{unique_id} "
                    f"from remote bucket {remote_bucket_name}"
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "download_error",
                        "message": "Failed to load experiment data, "
                        "please try again later",
                    },
                )

        # Download input files (TIFF/CSV) needed for viewing (Issue D fix)
        try:
            from studio.app.common.core.snakemake.smk_utils import SmkUtils

            input_filenames = SmkUtils.get_datatypes_inputs(
                workspace_id, unique_id, apply_basename=True
            )
            if input_filenames:
                controller = RemoteStorageController(remote_bucket_name)
                for input_filename in input_filenames:
                    downloaded = await controller.download_input_data(
                        workspace_id, input_filename
                    )
                    if not downloaded:
                        logger.warning(
                            f"Input file not found in S3: "
                            f"{workspace_id}/{input_filename}"
                        )
        except (AssertionError, KeyError):
            # snakemake_config.yaml missing or incomplete
            pass
        except Exception as e:
            logger.warning(
                f"Input file download failed for {workspace_id}/{unique_id}: {e}"
            )

    # Validate experiment is displayable (checks if required files exist locally)
    # This should be done BEFORE checking sync status, because on-demand download
    # may have just succeeded
    display_validation = PublishValidator.validate_for_display(
        workspace_id=workspace_id,
        unique_id=unique_id,
    )
    if not display_validation.is_displayable:
        # Data is not available locally - check if we should return pending or error
        if hasattr(record, "local_sync_status"):
            if record.local_sync_status == LocalSyncStatus.pending.value:
                logger.info(
                    f"Experiment {workspace_id}/{unique_id} is pending sync "
                    f"and not yet available locally"
                )
                return JSONResponse(
                    status_code=202,
                    content={
                        "status": "pending_sync",
                        "message": (
                            "Publishing in progress, check back in a few minutes. "
                            "Experiments are typically available within 5 minutes."
                        ),
                        "retry_after": 300,
                    },
                    headers={"Retry-After": "300"},
                )
        # For error status or no status, return data error
        logger.error(
            f"Experiment {workspace_id}/{unique_id} is not displayable: "
            f"{display_validation.reason}"
        )
        return JSONResponse(
            status_code=503,
            content={
                "status": "data_error",
                "message": display_validation.reason
                or "Experiment data is unavailable",
            },
        )

    # Data is valid and available locally - update sync status if needed
    if hasattr(record, "local_sync_status"):
        if record.local_sync_status != LocalSyncStatus.synced.value:
            logger.info(
                f"Experiment {workspace_id}/{unique_id} data is available, "
                f"updating sync status from '{record.local_sync_status}' to 'synced'"
            )
            from sqlalchemy import update

            stmt = (
                update(models.ExperimentRecord)
                .where(models.ExperimentRecord.id == record.id)
                .where(models.ExperimentRecord.version == record.version)
                .values(
                    local_sync_status=LocalSyncStatus.synced.value,
                    version=models.ExperimentRecord.version + 1,
                )
            )
            result = db.execute(stmt)
            db.commit()
            if result.rowcount == 0:
                logger.info(
                    f"Experiment {workspace_id}/{unique_id} sync status "
                    f"already updated by another process"
                )

    return await reproduce_experiment(workspace_id, unique_id)


async def _validate_experiment_exists_in_s3(
    workspace_id: str, unique_id: str, bucket_name: str
) -> Tuple[bool, Optional[str]]:
    """
    Check if experiment data exists in S3.

    Args:
        workspace_id: The workspace ID
        unique_id: The experiment unique ID
        bucket_name: The S3 bucket name

    Returns:
        Tuple of (exists, error_message). If exists is True, error_message is None.
    """
    if not RemoteStorageController.is_available():
        return True, None  # Skip validation if S3 not configured

    s3_controller = S3StorageController(bucket_name)
    s3_path = S3StorageController.make_s3_output_prefix(workspace_id, unique_id)

    try:
        async with s3_controller._S3StorageController__get_s3_client() as client:
            result = await client.list_objects_v2(
                Bucket=bucket_name, Prefix=s3_path, MaxKeys=5
            )
            if result.get("KeyCount", 0) == 0:
                return False, f"No data found in S3 for {workspace_id}/{unique_id}"
    except Exception as e:
        logger.error(f"S3 validation error for {workspace_id}/{unique_id}: {e}")
        return False, f"Could not verify S3 data: {str(e)}"

    return True, None


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

            # Validate publish eligibility when publishing
            if flag == PublishFlags.on:
                validation = PublishValidator.validate(
                    workspace_id=str(record.workspace_id),
                    unique_id=record.uid,
                    user_has_s3_bucket=bool(current_user.remote_bucket_name),
                    check_files_on_disk=True,
                )
                if not validation.can_publish:
                    raise HTTPException(
                        status_code=400,
                        detail=validation.reason,
                    )

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

            # Validate S3 data exists before publishing
            if flag == PublishFlags.on:
                remote_storage_type = RemoteStorageType.get_activated_type()
                if remote_storage_type == RemoteStorageType.S3:
                    bucket_name = current_user.remote_bucket_name or os.environ.get(
                        "S3_DEFAULT_BUCKET_NAME"
                    )
                    if bucket_name:
                        exists, error_msg = await _validate_experiment_exists_in_s3(
                            str(record.workspace_id),
                            record.uid,
                            bucket_name,
                        )
                        if not exists:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Cannot publish: {error_msg}",
                            )
                else:
                    # Currently not supported
                    pass

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
- Validates each record before publishing; fails if any record cannot be published
""",
)
def multiple_publish_dataview_records(
    ids: List[int],
    flag: PublishFlags,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Validate all records before publishing
    if flag == PublishFlags.on:
        # First check S3 bucket (applies to all)
        if not current_user.remote_bucket_name:
            raise HTTPException(
                status_code=400,
                detail="Cannot publish data: No cloud storage bucket configured "
                "for your account. Please contact support to enable publishing.",
            )

        # Validate each record
        failed_records = []
        for record_id in ids:
            record = DataviewService.find_user_owned_dataview_record(
                db, record_id, current_user.id
            )
            if not record:
                continue  # Skip records not owned by user

            validation = PublishValidator.validate(
                workspace_id=str(record.workspace_id),
                unique_id=record.uid,
                user_has_s3_bucket=True,  # Already checked above
                check_files_on_disk=True,
            )
            if not validation.can_publish:
                failed_records.append(
                    {
                        "id": record_id,
                        "name": record.name,
                        "reason": validation.reason,
                    }
                )

        if failed_records:
            # Return error with details about which records failed
            detail = "Some experiments cannot be published:\n"
            for rec in failed_records[:5]:  # Limit to first 5
                detail += f"- {rec['name']}: {rec['reason']}\n"
            if len(failed_records) > 5:
                detail += f"... and {len(failed_records) - 5} more"
            raise HTTPException(status_code=400, detail=detail)

    DataviewService.multiple_publish_dataview_records(db, current_user.id, ids, flag)

    return True
