"""
Workflow Tracking for Free Tier Users

This module provides functions to track active workflows for free tier users.
This enables the Free Manager Lambda to:
1. Know when a user has active workflows running
2. Avoid migrating users with running workflows
3. Only migrate idle users safely
"""

from typing import Optional

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.mode import MODE
from studio.app.common.db.database import session_scope

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
        with session_scope() as session:
            # Only update if user exists in free_user_assignments
            from sqlalchemy import text

            query = text(
                """
                UPDATE free_user_assignments
                SET active_workflow_count = active_workflow_count + 1,
                    last_workflow_start = NOW()
                WHERE user_id = :user_id
            """
            )
            result = session.execute(query, {"user_id": str(user_id)})

            if result.rowcount > 0:
                logger.info(
                    f"Incremented workflow count for user {user_id} "
                    f"(free tier workflow started)"
                )
            else:
                logger.debug(
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

    Args:
        user_id: User ID of the user whose workflow completed
    """
    if MODE.IS_STANDALONE or user_id is None:
        return

    try:
        with session_scope() as session:
            # Ensure count doesn't go below 0
            from sqlalchemy import text

            query = text(
                """
                UPDATE free_user_assignments
                SET active_workflow_count = GREATEST(0, active_workflow_count - 1),
                    last_workflow_end = NOW()
                WHERE user_id = :user_id
            """
            )
            result = session.execute(query, {"user_id": str(user_id)})

            if result.rowcount > 0:
                logger.info(
                    f"Decremented workflow count for user {user_id} "
                    f"(free tier workflow completed)"
                )
            else:
                logger.debug(
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
            from sqlalchemy import text

            query = text(
                """
                SELECT active_workflow_count
                FROM free_user_assignments
                WHERE user_id = :user_id
            """
            )
            result = session.execute(query, {"user_id": str(user_id)})
            row = result.fetchone()

            if row:
                return row[0] or 0
            return 0

    except Exception as e:
        logger.error(
            f"Failed to get workflow count for user {user_id}: {e}",
            exc_info=True,
        )
        return 0
