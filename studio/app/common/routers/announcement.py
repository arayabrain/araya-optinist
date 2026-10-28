import hashlib
import json
import os
import time
from datetime import datetime, timezone
from functools import lru_cache

import boto3
from botocore.config import Config
from fastapi import APIRouter

from studio.app.common.core.logger import AppLogger

router = APIRouter(tags=["announcement"])

logger = AppLogger.get_logger()

ANNOUNCEMENT_SSM_PARAMETER_ENV = "ANNOUNCEMENT_SSM_PARAMETER"
CACHE_TTL_SECONDS = 60
MAX_MESSAGE_LENGTH = 500

_cache: dict = {"expires_at": 0.0, "value": {}}


@lru_cache(maxsize=1)
def _ssm_client():
    return boto3.client(
        "ssm",
        config=Config(connect_timeout=2, read_timeout=2, retries={"max_attempts": 1}),
    )


def parse_announcement(raw: str, now: datetime) -> dict:
    """Return {"id", "message"} for a live announcement, {} otherwise."""
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("Announcement parameter is not JSON; showing nothing")
        return {}
    if not isinstance(data, dict) or not data:
        return {}
    message = data.get("message")
    until_raw = data.get("until")
    try:
        until = datetime.fromisoformat(until_raw)
    except (TypeError, ValueError):
        until = None
    if not isinstance(message, str) or not message.strip() or until is None:
        logger.warning("Announcement needs a message and an ISO 8601 until")
        return {}
    if until.tzinfo is None:
        logger.warning("Announcement until has no timezone; showing nothing")
        return {}
    if until <= now:
        return {}
    message = message.strip()[:MAX_MESSAGE_LENGTH]
    return {
        "id": hashlib.sha1(message.encode("utf-8")).hexdigest()[:8],
        "message": message,
    }


def _load_announcement(parameter_name: str) -> dict:
    try:
        response = _ssm_client().get_parameter(Name=parameter_name)
        raw = response["Parameter"]["Value"]
    except Exception as e:
        logger.warning(f"Announcement read from SSM failed: {e}")
        return {}
    return parse_announcement(raw, datetime.now(timezone.utc))


@router.get("/announcement")
def get_announcement() -> dict:
    parameter_name = os.environ.get(ANNOUNCEMENT_SSM_PARAMETER_ENV)
    if not parameter_name:
        return {}
    now = time.monotonic()
    if now >= _cache["expires_at"]:
        _cache["value"] = _load_announcement(parameter_name)
        _cache["expires_at"] = now + CACHE_TTL_SECONDS
    return _cache["value"]
