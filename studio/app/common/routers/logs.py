import logging
import os
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from typing_extensions import Optional

from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.platform_metadata import ECS_SERVICE_NAME, ECS_TASK_ID
from studio.app.common.core.logger import VALID_LOG_LEVELS, AppLogger
from studio.app.common.core.utils.log_reader import LogLevel, LogReader
from studio.app.common.schemas.outputs import PaginatedLineResult, PlatformInfo
from studio.app.common.schemas.users import User

router = APIRouter(prefix="/logs", tags=["logs"])

logger = AppLogger.get_logger()

LOG_LEVEL_NUMERIC = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

# Read once at startup; controls which levels the frontend log viewer offers
_UI_MIN_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()
if _UI_MIN_LEVEL not in VALID_LOG_LEVELS:
    _UI_MIN_LEVEL = "DEBUG"
_UI_MIN_NUMERIC = LOG_LEVEL_NUMERIC.get(_UI_MIN_LEVEL, logging.DEBUG)


@router.get(
    "/level",
    summary="Return available log filter levels for the frontend viewer",
)
async def get_available_log_levels(
    _current_user: User = Depends(get_current_user),
):
    levels = [
        name
        for name, numeric in LOG_LEVEL_NUMERIC.items()
        if numeric >= _UI_MIN_NUMERIC
    ]
    levels.append("FRONTEND")
    return {"levels": levels}


@router.get(
    "",
    summary="Fetch log data with pagination",
)
async def get_log_data(
    current_user: User = Depends(get_current_user),
    offset: int = Query(
        default=-1,
        ge=-1,
        description="The starting position in the log file from which to fetch data."
        "A value of `-1` indicates the request should start from the end of the file",
    ),
    limit: int = Query(
        default=50,
        ge=0,
        description="Max number of log unit to return.",
    ),
    reverse: bool = Query(
        default=True,
        description="Fetch logs in reverse order.",
    ),
    search: Optional[str] = Query(default=None),
    levels: List[LogLevel] = Query(default=[LogLevel.ALL]),
):
    try:
        platform = PlatformInfo(service_name=ECS_SERVICE_NAME, task_id=ECS_TASK_ID)
        stop_offset = None
        log_reader = LogReader(
            levels=levels, filter_user_id=current_user.uid if current_user else None
        )

        if search:
            stop_offset, offset = log_reader.get_unit_position_from_search_text(
                search, offset, reverse, search_match_case=False
            )
            if stop_offset is None:
                return PaginatedLineResult(
                    next_offset=offset,
                    prev_offset=offset,
                    data=[],
                    platform=platform,
                )

            logs = log_reader.read_from_offset(
                offset=stop_offset if reverse else offset,
                stop_offset=offset if reverse else stop_offset,
                reverse=False,
            )
            extra_logs = log_reader.read_from_offset(
                offset=logs.prev_offset if reverse else logs.next_offset,
                stop_offset=None,
                limit=limit,
                reverse=reverse,
            )
            return PaginatedLineResult(
                next_offset=max(logs.next_offset, extra_logs.next_offset),
                prev_offset=min(logs.prev_offset, extra_logs.prev_offset),
                data=(
                    extra_logs.data + logs.data
                    if reverse
                    else logs.data + extra_logs.data
                ),
                platform=platform,
            )

        result = log_reader.read_from_offset(
            offset=offset,
            stop_offset=stop_offset,
            limit=limit,
            reverse=reverse,
        )
        result.platform = platform
        return result
    except Exception as e:
        logger.error(e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
