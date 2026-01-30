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

from sqlmodel import select

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.mode import MODE
from studio.app.common.db.database import session_scope
from studio.app.common.models import FreeUserAssignment, PremiumUserAssignment

logger = AppLogger.get_logger()

# Tier constants
TIER_FREE = "free"
TIER_PREMIUM = "premium"


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


def increment_workflow_count(user_id: Optional[int]) -> None:
    """
    Increment the active_workflow_count for a user (free or premium tier).

    Called when a workflow starts. Checks user's subscription tier first,
    then updates the appropriate table. Falls back to the other table if
    the primary table doesn't have a record.

    Args:
        user_id: User ID of the user starting the workflow
    """
    if MODE.IS_STANDALONE or user_id is None:
        return

    try:
        from sqlalchemy import func, update

        # Determine user's tier and which tables have records
        tier, has_free_record, has_premium_record = _get_user_tier(user_id)

        logger.info(
            f"Workflow count increment for user {user_id}: "
            f"tier={tier}, has_free={has_free_record}, has_premium={has_premium_record}"
        )

        with session_scope() as session:
            updated = False

            # Priority 1: Update based on actual subscription tier
            if tier == TIER_PREMIUM and has_premium_record:
                new_count = PremiumUserAssignment.active_workflow_count + 1
                stmt = (
                    update(PremiumUserAssignment)
                    .where(PremiumUserAssignment.user_id == user_id)
                    .values(
                        active_workflow_count=new_count,
                        last_workflow_start=func.now(),
                    )
                )
                result = session.execute(stmt)
                if result.rowcount > 0:
                    session.commit()
                    logger.info(
                        f"Incremented workflow count for user {user_id} "
                        "(premium tier - primary)"
                    )
                    updated = True

            elif tier == TIER_FREE and has_free_record:
                new_count = FreeUserAssignment.active_workflow_count + 1
                stmt = (
                    update(FreeUserAssignment)
                    .where(FreeUserAssignment.user_id == user_id)
                    .values(
                        active_workflow_count=new_count,
                        last_workflow_start=func.now(),
                    )
                )
                result = session.execute(stmt)
                if result.rowcount > 0:
                    session.commit()
                    logger.info(
                        f"Incremented workflow count for user {user_id} "
                        f"(free tier - primary)"
                    )
                    updated = True

            # Priority 2: Fallback - try available record if tier didn't match
            if not updated:
                # Try premium first (preferred for premium users without record)
                if has_premium_record:
                    new_count = PremiumUserAssignment.active_workflow_count + 1
                    stmt = (
                        update(PremiumUserAssignment)
                        .where(PremiumUserAssignment.user_id == user_id)
                        .values(
                            active_workflow_count=new_count,
                            last_workflow_start=func.now(),
                        )
                    )
                    result = session.execute(stmt)
                    if result.rowcount > 0:
                        session.commit()
                        logger.info(
                            f"Incremented workflow count for user {user_id} "
                            "(premium tier - fallback)"
                        )
                        updated = True

                elif has_free_record:
                    new_count = FreeUserAssignment.active_workflow_count + 1
                    stmt = (
                        update(FreeUserAssignment)
                        .where(FreeUserAssignment.user_id == user_id)
                        .values(
                            active_workflow_count=new_count,
                            last_workflow_start=func.now(),
                        )
                    )
                    result = session.execute(stmt)
                    if result.rowcount > 0:
                        session.commit()
                        logger.info(
                            f"Incremented workflow count for user {user_id} "
                            "(free tier - fallback)"
                        )
                        updated = True

            if not updated:
                logger.warning(
                    f"User {user_id} not in free or premium assignments table "
                    f"(tier={tier}, no records found)"
                )

    except Exception as e:
        logger.error(
            f"Failed to increment workflow count for user {user_id}: {e}",
            exc_info=True,
        )


def decrement_workflow_count(user_id: Optional[int]) -> None:
    """
    Decrement the active_workflow_count for a user (free or premium tier).

    Called when a workflow completes (success or failure). Checks user's
    subscription tier first, then updates the appropriate table. Falls back
    to the other table if the primary table doesn't have a record.

    Uses SQLAlchemy's update() with func.greatest() to ensure count never
    goes below 0, preventing race conditions.

    Args:
        user_id: User ID of the user whose workflow completed
    """
    if MODE.IS_STANDALONE or user_id is None:
        return

    try:
        from sqlalchemy import func, update

        # Determine user's tier and which tables have records
        tier, has_free_record, has_premium_record = _get_user_tier(user_id)

        logger.info(
            f"Workflow count decrement for user {user_id}: "
            f"tier={tier}, has_free={has_free_record}, has_premium={has_premium_record}"
        )

        with session_scope() as session:
            updated = False

            # Priority 1: Update based on actual subscription tier
            if tier == TIER_PREMIUM and has_premium_record:
                current = PremiumUserAssignment.active_workflow_count
                new_count = func.greatest(0, current - 1)
                stmt = (
                    update(PremiumUserAssignment)
                    .where(PremiumUserAssignment.user_id == user_id)
                    .values(
                        active_workflow_count=new_count,
                        last_workflow_end=func.now(),
                    )
                )
                result = session.execute(stmt)
                if result.rowcount > 0:
                    session.commit()
                    logger.info(
                        f"Decremented workflow count for user {user_id} "
                        "(premium tier - primary)"
                    )
                    updated = True

            elif tier == TIER_FREE and has_free_record:
                current = FreeUserAssignment.active_workflow_count
                new_count = func.greatest(0, current - 1)
                stmt = (
                    update(FreeUserAssignment)
                    .where(FreeUserAssignment.user_id == user_id)
                    .values(
                        active_workflow_count=new_count,
                        last_workflow_end=func.now(),
                    )
                )
                result = session.execute(stmt)
                if result.rowcount > 0:
                    session.commit()
                    logger.info(
                        f"Decremented workflow count for user {user_id} "
                        "(free tier - primary)"
                    )
                    updated = True

            # Priority 2: Fallback - try available record if tier didn't match
            if not updated:
                # Try premium first (preferred for premium users without record)
                if has_premium_record:
                    current = PremiumUserAssignment.active_workflow_count
                    new_count = func.greatest(0, current - 1)
                    stmt = (
                        update(PremiumUserAssignment)
                        .where(PremiumUserAssignment.user_id == user_id)
                        .values(
                            active_workflow_count=new_count,
                            last_workflow_end=func.now(),
                        )
                    )
                    result = session.execute(stmt)
                    if result.rowcount > 0:
                        session.commit()
                        logger.info(
                            f"Decremented workflow count for user {user_id} "
                            "(premium tier - fallback)"
                        )
                        updated = True

                elif has_free_record:
                    current = FreeUserAssignment.active_workflow_count
                    new_count = func.greatest(0, current - 1)
                    stmt = (
                        update(FreeUserAssignment)
                        .where(FreeUserAssignment.user_id == user_id)
                        .values(
                            active_workflow_count=new_count,
                            last_workflow_end=func.now(),
                        )
                    )
                    result = session.execute(stmt)
                    if result.rowcount > 0:
                        session.commit()
                        logger.info(
                            f"Decremented workflow count for user {user_id} "
                            "(free tier - fallback)"
                        )
                        updated = True

            if not updated:
                logger.warning(
                    f"User {user_id} not in free or premium assignments table "
                    f"(tier={tier}, no records found)"
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
