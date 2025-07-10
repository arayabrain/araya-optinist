from typing import List, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException
from fastapi_pagination.ext.sqlmodel import paginate
from sqlalchemy.sql import Select
from sqlmodel import Session, select

from studio.app.common import models as common_model
from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.logger import AppLogger
from studio.app.common.db.database import get_db
from studio.app.common.schemas.base import SortDirection, SortOptions
from studio.app.common.schemas.dataview import (
    DataviewRecord,
    DataviewRecordHeader,
    DataviewRecordSearchOptions,
    PageWithHeader,
    PublishFlags,
)
from studio.app.common.schemas.users import User

router = APIRouter(tags=["Dataview"])
public_router = APIRouter(tags=["Dataview"])

logger = AppLogger.get_logger()


def experiment_transformer(items: Sequence) -> Sequence:
    experiments = []

    for item in items:
        # TODO: Planned to implement
        pass

    return experiments


def get_search_db_experiment_query(
    query: Select, options: DataviewRecordSearchOptions
) -> Select:
    if options.uid is not None:
        query = query.filter(
            common_model.ExperimentRecord.uid.like("%{0}%".format(options.uid))
        )

    return query


@router.get(
    # "/dataview",
    "/expdb/experiments",
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
        sa_table=common_model.ExperimentRecord,
        mapping={},
    )
    query = select(common_model.ExperimentRecord)

    query = get_search_db_experiment_query(query, options)

    # TODO: Planned to implement
    # if publish_status is not None:
    #    query = query.filter(common_model.ExperimentRecord.publish_status == publish_status)

    query = query.group_by(common_model.ExperimentRecord.id).order_by(*sa_sort_list)

    data: PageWithHeader = paginate(
        session=db,
        query=query,
        # transformer=experiment_transformer, # TODO: Planned to implement
        transformer=None,  # TODO: Planned to implement
    )

    return data
