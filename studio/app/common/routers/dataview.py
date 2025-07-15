from typing import List, Sequence

from fastapi import APIRouter, Depends, HTTPException
from fastapi_pagination.ext.sqlmodel import paginate
from sqlalchemy.sql import Select
from sqlmodel import Session, select

from studio.app.common import models
from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.logger import AppLogger
from studio.app.common.db.database import get_db
from studio.app.common.schemas.base import SortOptions
from studio.app.common.schemas.dataview import (
    DataviewRecord,
    DataviewRecordSearchOptions,
    PageWithHeader,
    PublishFlags,
    PublishStatus,
)
from studio.app.common.schemas.users import User

router = APIRouter(tags=["Dataview"])
public_router = APIRouter(tags=["Dataview"])

logger = AppLogger.get_logger()


RECORDS_SORT_MAPPING = {
    "user_name": models.User.name,
    "workspace_name": models.Workspace.name,
    "last_modified": models.ExperimentRecord.updated_at,
}


def records_pagenate_transformer(items: Sequence) -> Sequence:
    records = []

    for item in items:
        record = DataviewRecord.from_orm(item)

        # Adjusting response fields
        record.owner = record.workspace.user
        record.workspace.user = None

        records.append(record)

    return records


def get_search_db_experiment_query(
    query: Select, options: DataviewRecordSearchOptions
) -> Select:
    if options.uid:
        query = query.filter(
            models.ExperimentRecord.uid.like("%{0}%".format(options.uid))
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
    "/public/dataview",
    response_model=PageWithHeader[DataviewRecord],
    description="""
- Search and respond to data for display in Public Dataview
""",
)
async def search_public_dataview_records(
    db: Session = Depends(get_db),
    options: DataviewRecordSearchOptions = Depends(),
    sortOptions: SortOptions = Depends(),
):
    sa_sort_list = sortOptions.get_sa_sort_list(
        sa_table=models.ExperimentRecord,
        mapping=RECORDS_SORT_MAPPING,
    )

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
            models.ExperimentRecord.publish_status == PublishStatus.on.value,
        )
    )

    query = get_search_db_experiment_query(query, options)
    query = query.group_by(models.ExperimentRecord.id).order_by(*sa_sort_list)

    data: PageWithHeader = paginate(
        session=db,
        query=query,
        transformer=records_pagenate_transformer,
    )

    return data


@router.get(
    "/dataview",
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
    sa_sort_list = sortOptions.get_sa_sort_list(
        sa_table=models.ExperimentRecord,
        mapping=RECORDS_SORT_MAPPING,
    )

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
            models.User.id == current_user.id,
            models.User.active.is_(True),
        )
    )

    query = get_search_db_experiment_query(query, options)
    query = query.group_by(models.ExperimentRecord.id).order_by(*sa_sort_list)

    data: PageWithHeader = paginate(
        session=db,
        query=query,
        transformer=records_pagenate_transformer,
    )

    return data


@router.post(
    "/dataview/publish/{id}/{flag}",
    response_model=bool,
    description="""
- Publishing Dataview records
""",
)
async def publish_dataview_records(
    id: int,
    flag: PublishFlags,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = (
        db.query(models.ExperimentRecord)
        .join(
            models.Workspace,
            models.Workspace.id == models.ExperimentRecord.workspace_id,
        )
        .join(
            models.User,
            models.User.id == models.Workspace.user_id,
        )
        .filter(
            models.ExperimentRecord.id == id,
            models.User.id == current_user.id,
            models.User.active.is_(True),
        )
        .first()
    )

    if not record:
        raise HTTPException(status_code=404)

    record.publish_status = int(flag == PublishFlags.on)
    db.commit()

    return True


@router.post(
    "/dataview/multiple/publish/{flag}",
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
    db.query(models.ExperimentRecord).filter(
        models.Workspace.id == models.ExperimentRecord.workspace_id,
        models.User.id == models.Workspace.user_id,
        models.User.id == current_user.id,
        models.User.active.is_(True),
        models.ExperimentRecord.id.in_(ids),
    ).update(
        {models.ExperimentRecord.publish_status: int(flag == PublishFlags.on)},
        synchronize_session=False,
    )

    db.commit()

    return True
