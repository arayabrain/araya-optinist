"""
Workflow Tracking for Free and Premium Tier Users

This module provides functions to track active workflows for
both free and premium tier users.
This enables the Manager Lambdas to:
1. Know when a user has active workflows running
2. Avoid migrating/reassigning users with running workflows
3. Only migrate idle users safely
4. Recover from crashes where workflow counts get stuck
"""

from typing import Optional, Tuple

from sqlalchemy import case, func, update
from sqlmodel import select

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.mode import MODE
from studio.app.common.db.database import session_scope
from studio.app.common.models import FreeUserAssignment, PremiumUserAssignment

logger = AppLogger.get_logger()

# Tier constants
TIER_FREE = "free"
TIER_PREMIUM = "premium"

_TABLE_OF_TIER = {TIER_FREE: FreeUserAssignment, TIER_PREMIUM: PremiumUserAssignment}
_TIER_OF_TABLE = {table: tier for tier, table in _TABLE_OF_TIER.items()}

# The premium manager's assign moves a free row's count to the new premium row
# and deletes the free row; premium rows are deleted without a hand-over
_COUNT_HANDED_OVER_TO = {FreeUserAssignment: PremiumUserAssignment}


def _get_user_tier(user_id: int) -> Tuple[Optional[str], bool, bool]:
    """
    Determine user's subscription tier and check which assignment tables have records.

    Args:
        user_id: User ID to check

    Returns:
        Tuple of (tier, has_free_record, has_premium_record)
        - tier: TIER_FREE, TIER_PREMIUM, or None if can't determine
        - has_free_record: True if user has FreeUserAssignment record
        - has_premium_record: True if user has PremiumUserAssignment record
    """
    # Check which assignment tables have records for this user
    # Do this first, outside the try block, so we always have this info
    has_free_record = False
    has_premium_record = False

    try:
        with session_scope() as session:
            # Check free tier table
            free_stmt = select(FreeUserAssignment).where(
                FreeUserAssignment.user_id == user_id
            )
            free_result = session.execute(free_stmt).first()
            has_free_record = free_result is not None

            # Check premium tier table
            premium_stmt = select(PremiumUserAssignment).where(
                PremiumUserAssignment.user_id == user_id
            )
            premium_result = session.execute(premium_stmt).first()
            has_premium_record = premium_result is not None
    except Exception as e:
        logger.warning(f"Failed to check assignment records for user {user_id}: {e}")
        # Continue - we'll try to get tier and use fallback logic

    # Get actual subscription tier from database
    tier = None
    try:
        from studio.app.common.core.subscription.constants import SubscriptionPlanIds
        from studio.app.common.core.subscription.subscription_service import (
            SubscriptionService,
        )
        from studio.app.common.db.database import get_db
        from studio.app.common.models.user import User

        db = next(get_db())
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                logger.warning(f"User {user_id} not found in users table")
                return None, has_free_record, has_premium_record

            subscription_data = SubscriptionService.get_user_subscription(db, user_id)
            if subscription_data:
                _, plan = subscription_data
                tier = (
                    TIER_PREMIUM
                    if plan.id == SubscriptionPlanIds.PREMIUM
                    else TIER_FREE
                )
            else:
                tier = TIER_FREE
        finally:
            db.close()

    except Exception as e:
        logger.warning(
            f"Failed to determine subscription tier for user {user_id}: {e}. "
            f"Will use fallback based on existing records."
        )
        # tier remains None, fallback logic will be used

    return tier, has_free_record, has_premium_record


def _counted_table(
    tier: Optional[str], has_free_record: bool, has_premium_record: bool
) -> Optional[type]:
    """The assignment table a user's runs are counted on.

    - the subscription tier's table, when the user has that row
    - otherwise whichever row exists, premium first
    - None: no row, nothing to count on
    """
    if tier == TIER_PREMIUM and has_premium_record:
        return PremiumUserAssignment
    if tier == TIER_FREE and has_free_record:
        return FreeUserAssignment
    if has_premium_record:
        return PremiumUserAssignment
    if has_free_record:
        return FreeUserAssignment
    return None


def _execution_host() -> Optional[str]:
    """The EC2 instance this process runs on; None off EC2 (local, tests)."""
    from studio.app.common.core.middleware.user_activity_middleware import (
        _get_instance_id,
    )

    host = _get_instance_id()
    return host if host and host != "local" else None


def _increment_statement(table: type, user_id: int, host: Optional[str]):
    """The run-start UPDATE.

    - active_workflow_count: computed in SQL, so concurrent starts cannot lose one
    - instance_id (free tier, on EC2): the first workflow pins the row to this
      host; a later one does not move it. The free scale-in reads this column to
      spare the host a workflow runs on, so it must name that host, not the last
      instance that served a request
    - SET order is fixed: MySQL assigns left to right, so the pin's CASE must
      come before the count it reads
    """
    count = table.active_workflow_count
    values = []
    if table is FreeUserAssignment and host:
        values.append(
            (table.instance_id, case((count > 0, table.instance_id), else_=host))
        )
    values += [(count, count + 1), (table.last_workflow_start, func.now())]
    return update(table).where(table.user_id == user_id).ordered_values(*values)


def increment_workflow_count(user_id: Optional[int]) -> Optional[str]:
    """
    Count a starting workflow on the table _counted_table picks.

    Called when a workflow starts. Standalone, no assignment row, or a database
    error → not counted.

    Args:
        user_id: User ID of the user starting the workflow

    Returns:
        The tier whose table was counted, for the release; None if nothing was
        counted
    """
    if MODE.IS_STANDALONE or user_id is None:
        return None

    try:
        tier, has_free_record, has_premium_record = _get_user_tier(user_id)
        table = _counted_table(tier, has_free_record, has_premium_record)

        logger.info(
            f"Workflow count increment for user {user_id}: "
            f"tier={tier}, has_free={has_free_record}, has_premium={has_premium_record}"
        )

        if table is None:
            logger.warning(
                f"User {user_id} not in free or premium assignments table "
                f"(tier={tier}, no records found)"
            )
            return None

        statement = _increment_statement(table, user_id, _execution_host())

        with session_scope() as session:
            result = session.execute(statement)
            if result.rowcount > 0:
                session.commit()
                logger.debug(
                    f"Incremented workflow count for user {user_id} "
                    f"({table.__tablename__})"
                )
                return _TIER_OF_TABLE[table]

        logger.warning(
            f"User {user_id}'s {table.__tablename__} row went away before the "
            "workflow count increment"
        )
        return None

    except Exception as e:
        logger.error(
            f"Failed to increment workflow count for user {user_id}: {e}",
            exc_info=True,
        )
        return None


def _update_counted_row(session, table: type, user_id: int, statement_for):
    """Execute statement_for(table, user_id) on the row holding the run's count.

    - the counted table's row, while it exists
    - free row gone: the premium row its count was handed over to

    Returns:
        The table written; None when neither row exists
    """
    for candidate in (table, _COUNT_HANDED_OVER_TO.get(table)):
        if candidate is None:
            break
        if session.execute(statement_for(candidate, user_id)).rowcount > 0:
            return candidate
    return None


def _decrement_statement(table: type, user_id: int):
    count = table.active_workflow_count
    return (
        update(table)
        .where(table.user_id == user_id)
        .values(
            active_workflow_count=func.greatest(0, count - 1),
            last_workflow_end=func.now(),
        )
    )


def decrement_workflow_count(
    user_id: Optional[int], tier: Optional[str] = None
) -> None:
    """
    Decrement the active_workflow_count for a user (free or premium tier).

    Called when a workflow completes (success or failure), or when a counted
    start is not scheduled.

    - tier given (what increment_workflow_count returned): that table, so a
      plan lapse during the run cannot move the release to the other row
    - tier None: the table _counted_table picks now
    - either way, the row holding the count (_update_counted_row): after an
      upgrade during the run, the premium row the count was handed over to

    Uses SQLAlchemy's update() with func.greatest() to ensure count never
    goes below 0, preventing race conditions.

    Args:
        user_id: User ID of the user whose workflow completed
        tier: The tier the workflow was counted on, if known
    """
    if MODE.IS_STANDALONE or user_id is None:
        return

    try:
        if tier is None:
            # Determine user's tier and which tables have records
            tier, has_free_record, has_premium_record = _get_user_tier(user_id)
            table = _counted_table(tier, has_free_record, has_premium_record)

            logger.info(
                f"Workflow count decrement for user {user_id}: tier={tier}, "
                f"has_free={has_free_record}, has_premium={has_premium_record}"
            )
        else:
            table = _TABLE_OF_TIER.get(tier)

        if table is None:
            logger.warning(
                f"User {user_id} not in free or premium assignments table "
                f"(tier={tier}, no records found)"
            )
            return

        with session_scope() as session:
            written = _update_counted_row(session, table, user_id, _decrement_statement)
            if written is not None:
                session.commit()
                logger.debug(
                    f"Decremented workflow count for user {user_id} "
                    f"({written.__tablename__})"
                )
            else:
                logger.warning(
                    f"User {user_id}'s {table.__tablename__} row went away before "
                    "the workflow count decrement"
                )

    except Exception as e:
        logger.error(
            f"Failed to decrement workflow count for user {user_id}: {e}",
            exc_info=True,
        )


def get_active_workflow_count(user_id: int) -> int:
    """
    Get the current active_workflow_count for a user (free or premium tier).

    Checks user's subscription tier first to query the appropriate table.
    Falls back to checking both tables if tier lookup fails.

    Args:
        user_id: User ID to query

    Returns:
        Number of active workflows (0 if user not found or error)
    """
    if MODE.IS_STANDALONE:
        return 0

    try:
        # Determine user's tier and which tables have records
        tier, has_free_record, has_premium_record = _get_user_tier(user_id)

        with session_scope() as session:
            # Priority 1: Check based on subscription tier
            if tier == TIER_PREMIUM and has_premium_record:
                statement = select(PremiumUserAssignment).where(
                    PremiumUserAssignment.user_id == user_id
                )
                result_row = session.execute(statement).first()
                assignment = result_row[0] if result_row else None
                if assignment:
                    return assignment.active_workflow_count or 0

            elif tier == TIER_FREE and has_free_record:
                statement = select(FreeUserAssignment).where(
                    FreeUserAssignment.user_id == user_id
                )
                result_row = session.execute(statement).first()
                assignment = result_row[0] if result_row else None
                if assignment:
                    return assignment.active_workflow_count or 0

            # Priority 2: Fallback - check any available record
            if has_premium_record:
                statement = select(PremiumUserAssignment).where(
                    PremiumUserAssignment.user_id == user_id
                )
                result_row = session.execute(statement).first()
                assignment = result_row[0] if result_row else None
                if assignment:
                    return assignment.active_workflow_count or 0

            if has_free_record:
                statement = select(FreeUserAssignment).where(
                    FreeUserAssignment.user_id == user_id
                )
                result_row = session.execute(statement).first()
                assignment = result_row[0] if result_row else None
                if assignment:
                    return assignment.active_workflow_count or 0

            return 0

    except Exception as e:
        logger.error(
            f"Failed to get workflow count for user {user_id}: {e}",
            exc_info=True,
        )
        return 0
