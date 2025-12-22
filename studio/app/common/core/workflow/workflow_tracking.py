"""
Workflow Tracking for Free Tier Users

This module provides functions to track active workflows for free tier users.
This enables the Free Manager Lambda to:
1. Know when a user has active workflows running
2. Avoid migrating users with running workflows
3. Only migrate idle users safely
"""

from typing import Optional

from sqlmodel import select

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.mode import MODE
from studio.app.common.db.database import session_scope
from studio.app.common.models import FreeUserAssignment

logger = AppLogger.get_logger()


def increment_workflow_count(user_id: Optional[int]) -> None:
    """
    Increment the active_workflow_count for a free tier user.

    Called when a workflow starts.

    Args:
        user_id: User ID of the user starting the workflow
    """
    if MODE.IS_STANDALONE or user_id is None:
        return

    try:
        from sqlalchemy import func, update

        with session_scope() as session:
            # Use SQLAlchemy's update() for atomic increment (prevents race conditions)
            stmt = (
                update(FreeUserAssignment)
                .where(FreeUserAssignment.user_id == str(user_id))
                .values(
                    active_workflow_count=FreeUserAssignment.active_workflow_count + 1,
                    last_workflow_start=func.now(),
                )
            )

            result = session.execute(stmt)
            session.commit()

            if result.rowcount > 0:
                logger.info(
                    f"Incremented workflow count for user {user_id} "
                    f"(free tier workflow started)"
                )
            else:
                logger.info(
                    f"User {user_id} not in free_user_assignments table "
                    f"(likely premium user or not tracked yet)"
                )

    except Exception as e:
        logger.error(
            f"Failed to increment workflow count for user {user_id}: {e}",
            exc_info=True,
        )


def decrement_workflow_count(user_id: Optional[int]) -> None:
    """
    Decrement the active_workflow_count for a free tier user.

    Called when a workflow completes (success or failure).

    Uses SQLAlchemy's update() with func.greatest() to ensure count never
    goes below 0, preventing race conditions.

    Args:
        user_id: User ID of the user whose workflow completed
    """
    if MODE.IS_STANDALONE or user_id is None:
        return

    try:
        from sqlalchemy import func, update

        with session_scope() as session:
            # Use SQLAlchemy's update() with greatest() for atomic decrement
            stmt = (
                update(FreeUserAssignment)
                .where(FreeUserAssignment.user_id == str(user_id))
                .values(
                    active_workflow_count=func.greatest(
                        0, FreeUserAssignment.active_workflow_count - 1
                    ),
                    last_workflow_end=func.now(),
                )
            )

            result = session.execute(stmt)
            session.commit()

            if result.rowcount > 0:
                logger.info(
                    f"Decremented workflow count for user {user_id} "
                    f"(free tier workflow completed)"
                )
            else:
                logger.info(
                    f"User {user_id} not in free_user_assignments table "
                    f"(likely premium user or not tracked yet)"
                )

    except Exception as e:
        logger.error(
            f"Failed to decrement workflow count for user {user_id}: {e}",
            exc_info=True,
        )


def get_active_workflow_count(user_id: int) -> int:
    """
    Get the current active_workflow_count for a user.

    Args:
        user_id: User ID to query

    Returns:
        Number of active workflows (0 if user not found or error)
    """
    if MODE.IS_STANDALONE:
        return 0

    try:
        with session_scope() as session:
            statement = select(FreeUserAssignment).where(
                FreeUserAssignment.user_id == str(user_id)
            )
            assignment = session.exec(statement).first()

            if assignment:
                return assignment.active_workflow_count or 0
            return 0

    except Exception as e:
        logger.error(
            f"Failed to get workflow count for user {user_id}: {e}",
            exc_info=True,
        )
        return 0
