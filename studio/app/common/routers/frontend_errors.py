import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.utils.log_reader import FRONTEND_LOG_PREFIX
from studio.app.common.schemas.users import User

router = APIRouter(prefix="/users/me", tags=["logs"])

logger = AppLogger.get_logger()

FRONTEND_VALID_LEVELS = ("error", "warn")
_FRONTEND_LEVEL_PATTERN = r"^(" + "|".join(FRONTEND_VALID_LEVELS) + r")$"
_MAX_BATCH_SIZE = 20
# Allow slightly more than the frontend's 2000-char truncation limit
# to avoid rejecting messages right at the boundary
_MAX_MESSAGE_LENGTH = 2100


class FrontendErrorItem(BaseModel):
    level: str = Field(..., regex=_FRONTEND_LEVEL_PATTERN)
    message: str = Field(..., max_length=_MAX_MESSAGE_LENGTH)
    source: Optional[str] = None
    url: Optional[str] = None
    timestamp: Optional[str] = None


class FrontendErrorBatch(BaseModel):
    errors: List[FrontendErrorItem] = Field(..., max_items=_MAX_BATCH_SIZE)


# In-memory rate limiter: user_id -> list of request timestamps
_frontend_error_timestamps: Dict[int, list] = {}
_RATE_LIMIT_MAX = 10
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_CLEANUP_THRESHOLD = 100


def _cleanup_stale_rate_limits() -> None:
    """Remove entries for users whose timestamps have all expired."""
    now = time.time()
    expired = [
        uid
        for uid, ts in _frontend_error_timestamps.items()
        if not ts or all(now - t >= _RATE_LIMIT_WINDOW for t in ts)
    ]
    for uid in expired:
        del _frontend_error_timestamps[uid]


@router.post("/frontend-errors")
async def log_frontend_errors(
    batch: FrontendErrorBatch,
    current_user: User = Depends(get_current_user),
):
    """
    Receive batched frontend errors and log them with a [FRONTEND] prefix
    so they appear in the same CloudWatch log stream as backend logs.
    """
    now = time.time()
    user_id = current_user.id

    # Periodically prune stale entries to prevent unbounded growth
    if len(_frontend_error_timestamps) > _RATE_LIMIT_CLEANUP_THRESHOLD:
        _cleanup_stale_rate_limits()

    # Sliding window rate limit
    timestamps = _frontend_error_timestamps.get(user_id, [])
    timestamps = [t for t in timestamps if now - t < _RATE_LIMIT_WINDOW]

    if len(timestamps) >= _RATE_LIMIT_MAX:
        _frontend_error_timestamps[user_id] = timestamps
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Frontend error rate limit exceeded",
        )

    timestamps.append(now)

    count = 0
    for item in batch.errors:
        level_tag = item.level.upper()
        url_part = f" url={item.url}" if item.url else ""
        source_part = f" source={item.source}" if item.source else ""
        log_fn = logger.error if item.level == "error" else logger.warning
        log_fn(
            "%s [%s] user=%s%s%s: %s",
            FRONTEND_LOG_PREFIX,
            level_tag,
            current_user.uid,
            url_part,
            source_part,
            item.message,
        )
        count += 1

    _frontend_error_timestamps[user_id] = timestamps
    return {"count": count}
