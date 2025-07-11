from typing import Optional, Sequence

from fastapi import APIRouter, Depends
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
)
from studio.app.common.schemas.users import User

router = APIRouter(tags=["Dataview"])
public_router = APIRouter(tags=["Dataview"])

logger = AppLogger.get_logger()


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

    if options.workspace_name:
        query = query.filter(
            models.Workspace.name.like("%{0}%".format(options.workspace_name))
        )

    return query


@router.get(
    "/dataview",
    response_model=PageWithHeader[DataviewRecord],
    description="""
- Search and respond to data for display in Dataview
""",
)
async def search_dataview_records(
    db: Session = Depends(get_db),
    publish_status: Optional[bool] = None,
    options: DataviewRecordSearchOptions = Depends(),
    sortOptions: SortOptions = Depends(),
    current_user: User = Depends(get_current_user),
):
    sa_sort_list = sortOptions.get_sa_sort_list(
        sa_table=models.ExperimentRecord,
        mapping={
            "user_name": models.User.name,
            "workspace_name": models.Workspace.name,
            "last_modified": models.ExperimentRecord.updated_at,
        },
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

    # TODO: Planned to implement
    # if publish_status is not None:
    #    query = query.filter(models.ExperimentRecord.publish_status == publish_status)

    query = query.group_by(models.ExperimentRecord.id).order_by(*sa_sort_list)

    data: PageWithHeader = paginate(
        session=db,
        query=query,
        transformer=records_pagenate_transformer,
    )

    return data
