"""
Internal API endpoints for system-to-system communication.

These endpoints are protected by a shared secret (INTERNAL_API_SECRET)
rather than JWT authentication. They are called by Lambda functions
after user migrations to trigger experiment metadata sync.
"""

import os
import secrets
import time
from typing import Dict

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status
from sqlmodel import Session, select

from studio.app.common.core.auth.auth_dependencies import _get_user_remote_bucket_name
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    RemoteStorageSimpleReader,
)
from studio.app.common.db.database import get_db, get_session
from studio.app.common.models.user import User
from studio.app.common.models.workspace import Workspace

router = APIRouter(prefix="/internal", tags=["internal"])

logger = AppLogger.get_logger()

INTERNAL_API_SECRET = os.environ.get("INTERNAL_API_SECRET")

# Rate limiting for internal endpoints (in-memory cache)
_rate_limit_cache: Dict[int, float] = {}
_RATE_LIMIT_SECONDS = 10  # Allow one sync request per user per 10 seconds


def verify_internal_secret(x_internal_secret: str = Header(...)) -> None:
    """
    Verify the internal API secret header using constant-time comparison.
    Raises HTTPException 403 if invalid.
    """
    if not INTERNAL_API_SECRET:
        logger.error("INTERNAL_API_SECRET not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal API not configured",
        )

    # Use constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(x_internal_secret, INTERNAL_API_SECRET):
        logger.warning("Invalid internal API secret provided")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid internal secret",
        )


@router.post("/sync-experiments/{user_id}")
async def sync_user_experiments(
    user_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: None = Depends(verify_internal_secret),
):
    """
    Trigger experiment metadata sync for a user.

    Called by Lambda functions (free_manager, premium_manager) after
    migrating a user to a new instance. Downloads all experiment
    metadata from S3 to the local instance.

    Args:
        user_id: Database ID of the user to sync experiments for

    Returns:
        Status indicating sync was initiated
    """
    # Rate limiting check
    current_time = time.time()
    last_request = _rate_limit_cache.get(user_id, 0)
    if current_time - last_request < _RATE_LIMIT_SECONDS:
        logger.warning(
            f"Rate limited sync request for user {user_id}: "
            f"{current_time - last_request:.1f}s since last request"
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Sync request too frequent",
        )
    _rate_limit_cache[user_id] = current_time

    # Look up user (User.id is the integer primary key)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        logger.warning(f"Sync requested for non-existent user: {user_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Get bucket name
    bucket_name = _get_user_remote_bucket_name(user)

    logger.info(
        f"Initiating experiment sync for user {user_id} "
        f"(email: {user.email}, bucket: {bucket_name})"
    )

    # Trigger background download (non-blocking)
    background_tasks.add_task(_download_experiments_for_user, bucket_name, user_id)

    return {"status": "sync_initiated", "user_id": user_id}


async def _download_experiments_for_user(bucket_name: str, user_id: int) -> None:
    """
    Background task to download experiment metadata from S3.
    Creates its own database session to avoid using closed session.

    Args:
        bucket_name: S3 bucket name for the user
        user_id: User ID (for logging)
    """
    if not RemoteStorageController.is_available():
        logger.warning(
            f"Remote storage not available, skipping sync for user {user_id}"
        )
        return

    db = None
    try:
        # Create new database session for background task
        db = get_session()

        # Get all workspace IDs for this user
        # Workspace.user_id is the foreign key to User.id (integer)
        workspaces = db.exec(
            select(Workspace).where(Workspace.user_id == user_id)
        ).all()
        workspace_ids = [ws.id for ws in workspaces]

        if not workspace_ids:
            logger.info(f"No workspaces found for user {user_id}, skipping sync")
            return

        async with RemoteStorageSimpleReader(bucket_name) as controller:
            await controller.download_all_experiments_metas(workspace_ids)
        logger.info(f"Experiment sync completed for user {user_id}")
    except Exception as e:
        logger.error(f"Experiment sync failed for user {user_id}: {e}", exc_info=True)
    finally:
        if db:
            db.close()
