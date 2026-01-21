"""
Workflow Count Recovery for Free and Premium Tier Users

Handles recovery of active_workflow_count in case of process crashes.
This module provides a scheduled task that can be run by Lambda to:
1. Detect stale workflow counts (workflows marked as active but actually finished)
2. Reset counts to 0 for users with no running processes
3. Prevent workflow count leaks from process crashes

USAGE:
- Run as scheduled Lambda (e.g., every 5-10 minutes)
- Can also be manually triggered for immediate recovery
"""

from datetime import timedelta
from typing import List, Tuple

from sqlmodel import select

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.mode import MODE
from studio.app.common.core.utils.datetime_utils import get_current_datetime
from studio.app.common.db.database import session_scope
from studio.app.common.models import FreeUserAssignment

logger = AppLogger.get_logger()

# Consider workflow stale if last_workflow_start is older than this
STALE_WORKFLOW_THRESHOLD_MINUTES = 30


def recover_stale_workflow_counts(
    stale_threshold_minutes: int = STALE_WORKFLOW_THRESHOLD_MINUTES,
) -> Tuple[int, List[int]]:
    """
    Reset active_workflow_count to 0 for users with stale workflows.

    A workflow is considered stale if:
    - active_workflow_count > 0
    - last_workflow_start is older than threshold
    - This indicates a process crash without proper cleanup

    Args:
        stale_threshold_minutes: Minutes after which a workflow is considered stale

    Returns:
        Tuple of (number of users recovered, list of recovered user IDs)
    """
    if MODE.IS_STANDALONE:
        logger.info("Standalone mode - skipping workflow count recovery")
        return 0, []

    try:
        from sqlalchemy import update

        stale_cutoff = get_current_datetime() - timedelta(
            minutes=stale_threshold_minutes
        )
        recovered_users = []

        with session_scope() as session:
            # Find users with stale workflows
            stmt = select(FreeUserAssignment).where(
                FreeUserAssignment.active_workflow_count > 0,
                FreeUserAssignment.last_workflow_start < stale_cutoff,
            )
            stale_assignments_result = session.execute(stmt).all()

            if not stale_assignments_result:
                logger.info("No stale workflow counts found")
                return 0, []

            # Reset counts for stale workflows
            for row in stale_assignments_result:
                assignment = row[0]
                update_stmt = (
                    update(FreeUserAssignment)
                    .where(FreeUserAssignment.user_id == assignment.user_id)
                    .values(active_workflow_count=0)
                )
                session.execute(update_stmt)
                recovered_users.append(assignment.user_id)

                logger.warning(
                    f"Recovered stale workflow count for user {assignment.user_id}: "
                    f"count={assignment.active_workflow_count}, "
                    f"last_start={assignment.last_workflow_start}"
                )

            session.commit()

            logger.info(
                f"Recovered {len(recovered_users)} users with stale workflow counts"
            )
            return len(recovered_users), recovered_users

    except Exception as e:
        logger.error(f"Failed to recover stale workflow counts: {e}", exc_info=True)
        return 0, []
