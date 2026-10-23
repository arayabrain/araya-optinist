"""
Workflow Tracking for Free and Premium Tier Users

This module provides functions to track active workflows for
both free and premium tier users.
This enables the Manager Lambdas to:
1. Know when a user has active workflows running
2. Avoid migrating/reassigning users with running workflows
3. Only migrate idle users safely
4. Recover from crashes where workflow counts get stuck

It also gates run starts against the plan's concurrent-run limit
(``SubscriptionPlans.max_concurrent_workflows``, NULL = no limit).
"""

from dataclasses import dataclass
from typing import Optional, Tuple

from sqlalchemy import and_, case, func, not_, or_, text, update
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

# Workflow lease (last_workflow_heartbeat):
# - renewed every WORKFLOW_LEASE_RENEW_SECONDS while any counted run is alive
# - older than WORKFLOW_LEASE_TTL_SECONDS → no run alive; the gate does not count
#   the row's runs, so a run killed without its release never locks a user out
WORKFLOW_LEASE_RENEW_SECONDS = 60
WORKFLOW_LEASE_TTL_SECONDS = 600

WORKFLOW_CONCURRENCY_LIMIT_CODE = "workflow_concurrency_limit"


@dataclass(frozen=True)
class ConcurrencyLimit:
    """A plan's concurrent-run limit, for a plan that sets one"""

    plan_name: str
    limit: int
    # A plan that allows more runs at once, if one is offered
    upgrade_plan_name: Optional[str] = None


class WorkflowConcurrencyLimitError(Exception):
    """A run start refused: the user already runs as many as the plan allows"""

    def __init__(self, limit: ConcurrencyLimit, active: int):
        self.limit = limit
        self.active = active
        super().__init__(self.message)

    @property
    def message(self) -> str:
        runs = "workflow" if self.active == 1 else "workflows"
        message = (
            f"You already have {self.active} {runs} running, and the "
            f"{self.limit.plan_name} plan allows {self.limit.limit} at once. "
            "Wait for one to finish or cancel one, then run this workflow again. "
            "A run that stopped unexpectedly frees its place within about "
            f"{WORKFLOW_LEASE_TTL_SECONDS // 60} minutes."
        )
        if self.limit.upgrade_plan_name:
            message += (
                f" The {self.limit.upgrade_plan_name} plan allows more workflows "
                "at once."
            )
        return message

    def to_detail(self) -> dict:
        """The API error body (HTTP 429)"""
        return {
            "code": WORKFLOW_CONCURRENCY_LIMIT_CODE,
            "message": self.message,
            "plan": self.limit.plan_name,
            "limit": self.limit.limit,
            "active": self.active,
            "upgrade_available": self.limit.upgrade_plan_name is not None,
        }


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


def _get_concurrency_limit(user_id: int) -> Optional[ConcurrencyLimit]:
    """The concurrent-run limit of the user's plan; None = no limit.

    - plan: the active subscription's plan, else the Free plan
    - fails open: a lookup error or a value below 1 applies no limit
    """
    from studio.app.common.core.subscription.constants import SubscriptionPlanIds
    from studio.app.common.core.subscription.subscription_service import (
        SubscriptionService,
    )
    from studio.app.common.models.subscription import SubscriptionPlans

    try:
        with session_scope() as session:
            subscription = SubscriptionService.get_user_subscription(session, user_id)
            plan = (
                subscription[1]
                if subscription
                else session.get(SubscriptionPlans, SubscriptionPlanIds.FREE)
            )
            if plan is None or plan.max_concurrent_workflows is None:
                return None
            limit = plan.max_concurrent_workflows
            if not isinstance(limit, int) or limit < 1:
                logger.warning(
                    f"Plan {plan.id} has an invalid max_concurrent_workflows "
                    f"({limit!r}); no limit applied"
                )
                return None

            upgrade_plan_name = None
            if plan.id != SubscriptionPlanIds.PREMIUM:
                premium = session.get(SubscriptionPlans, SubscriptionPlanIds.PREMIUM)
                if (
                    premium is not None
                    and premium.status
                    and (
                        premium.max_concurrent_workflows is None
                        or premium.max_concurrent_workflows > limit
                    )
                ):
                    upgrade_plan_name = premium.name

            return ConcurrencyLimit(plan.name, limit, upgrade_plan_name)

    except Exception as e:
        logger.warning(
            f"Failed to read the concurrent-run limit for user {user_id}: {e}; "
            "no limit applied"
        )
        return None


def _execution_host() -> Optional[str]:
    """The EC2 instance this process runs on; None off EC2 (local, tests)."""
    from studio.app.common.core.middleware.user_activity_middleware import (
        _get_instance_id,
    )

    host = _get_instance_id()
    return host if host and host != "local" else None


def _lease_is_stale(table: type):
    """No live run has renewed the row's lease within WORKFLOW_LEASE_TTL_SECONDS.

    Compared on the database clock, the one the lease is written with.
    """
    heartbeat = table.last_workflow_heartbeat
    cutoff = func.date_sub(
        func.now(), text(f"INTERVAL {WORKFLOW_LEASE_TTL_SECONDS} SECOND")
    )
    return or_(heartbeat.is_(None), heartbeat < cutoff)


def _increment_statement(
    table: type, user_id: int, host: Optional[str], limit: Optional[int]
):
    """The run-start UPDATE.

    - active_workflow_count: computed in SQL, so concurrent starts cannot lose one
    - limit set: matches only below the limit or with a stale lease. The row
      lock makes check and increment one step, so two starts cannot both pass.
      A stale lease restarts the count at this run
    - instance_id (free tier, on EC2): the first workflow pins the row to this
      host; a later one does not move it. The free scale-in reads this column to
      spare the host a workflow runs on, so it must name that host, not the last
      instance that served a request
    - SET order is fixed: MySQL assigns left to right, so each CASE must come
      before the columns it reads
    """
    count = table.active_workflow_count
    statement = update(table).where(table.user_id == user_id)

    if limit is None:
        new_count = count + 1
        keeps_pin = count > 0
    else:
        stale = _lease_is_stale(table)
        statement = statement.where(or_(count < limit, stale))
        new_count = case((stale, 1), else_=count + 1)
        keeps_pin = and_(count > 0, not_(stale))

    values = []
    if table is FreeUserAssignment and host:
        values.append(
            (table.instance_id, case((keeps_pin, table.instance_id), else_=host))
        )
    values += [
        (count, new_count),
        (table.last_workflow_start, func.now()),
        (table.last_workflow_heartbeat, func.now()),
    ]
    return statement.ordered_values(*values)


def increment_workflow_count(user_id: Optional[int]) -> Optional[str]:
    """
    Count a starting workflow, gated by the plan's concurrent-run limit.

    Called when a workflow starts, before anything of the run is written.

    - counted on the table _counted_table picks
    - limit reached → WorkflowConcurrencyLimitError, nothing counted
    - standalone, no assignment row, or a database error → not counted and
      not refused

    Args:
        user_id: User ID of the user starting the workflow

    Returns:
        The tier whose table was counted, for the release and the lease;
        None if nothing was counted
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

        limit = _get_concurrency_limit(user_id)
        statement = _increment_statement(
            table, user_id, _execution_host(), limit.limit if limit else None
        )

        with session_scope() as session:
            result = session.execute(statement)
            if result.rowcount > 0:
                session.commit()
                logger.debug(
                    f"Incremented workflow count for user {user_id} "
                    f"({table.__tablename__})"
                )
                return _TIER_OF_TABLE[table]

            active = session.execute(
                select(table.active_workflow_count).where(table.user_id == user_id)
            ).scalar_one_or_none()

    except Exception as e:
        logger.error(
            f"Failed to increment workflow count for user {user_id}: {e}",
            exc_info=True,
        )
        return None

    if limit is None or active is None:
        logger.warning(
            f"User {user_id}'s {table.__tablename__} row went away before the "
            "workflow count increment"
        )
        return None

    logger.info(
        f"Workflow start refused for user {user_id}: {active} running, "
        f"{limit.plan_name} plan allows {limit.limit}"
    )
    raise WorkflowConcurrencyLimitError(limit, active)


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


def _renew_statement(table: type, user_id: int):
    return (
        update(table)
        .where(table.user_id == user_id)
        .values(last_workflow_heartbeat=func.now())
    )


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


def renew_workflow_lease(user_id: Optional[int], tier: Optional[str]) -> None:
    """
    Mark the user's counted runs alive (see WORKFLOW_LEASE_TTL_SECONDS).

    Written to the row holding the run's count (_update_counted_row). Errors
    are logged, not raised: a missed renewal can only let the gate admit a run
    early.

    Args:
        user_id: User ID of the running workflow's user
        tier: what increment_workflow_count returned; None = not counted
    """
    table = _TABLE_OF_TIER.get(tier)
    if MODE.IS_STANDALONE or user_id is None or table is None:
        return

    try:
        with session_scope() as session:
            _update_counted_row(session, table, user_id, _renew_statement)
    except Exception as e:
        logger.warning(f"Failed to renew the workflow lease for user {user_id}: {e}")


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
