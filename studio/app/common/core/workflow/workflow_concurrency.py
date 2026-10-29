"""
Per-plan limit on a user's concurrent workflow runs.

- limit: ``SubscriptionPlans.max_concurrent_workflows`` of the user's plan
  (NULL = no limit)
- one ``WorkflowRunSlot`` row per counted run, written only for a plan with a
  limit; the run renews it while it lives and deletes it when it ends
- a slot not renewed within WORKFLOW_SLOT_TTL_SECONDS belongs to a run that
  died and is not counted, so a crashed run never locks a user out
- independent of ``active_workflow_count`` (workflow_tracking), which cleanup
  and scale-in read: nothing here changes that count
"""

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import delete, func, insert, not_, text, update
from sqlmodel import select

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.mode import MODE
from studio.app.common.db.database import session_scope
from studio.app.common.models import User, WorkflowRunSlot

logger = AppLogger.get_logger()

WORKFLOW_SLOT_RENEW_SECONDS = 60
WORKFLOW_SLOT_TTL_SECONDS = 600

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
            f"{WORKFLOW_SLOT_TTL_SECONDS // 60} minutes."
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


def _slot_is_live():
    """Renewed within WORKFLOW_SLOT_TTL_SECONDS, on the database clock"""
    cutoff = func.date_sub(
        func.now(), text(f"INTERVAL {WORKFLOW_SLOT_TTL_SECONDS} SECOND")
    )
    return WorkflowRunSlot.heartbeat_at >= cutoff


def acquire_workflow_slot(
    user_id: Optional[int], workspace_id: str, unique_id: str
) -> Optional[int]:
    """
    Count a starting run against its user's plan limit.

    Called when a workflow starts, before anything of the run is written.

    - no limit, standalone, or a database error → no slot, not refused
    - one user's starts serialise on their users row (FOR UPDATE). The count
      after it is a plain read: its view starts after the lock, so it includes
      the previous start's slot, and it takes no gap locks that other users'
      starts could deadlock on
    - limit reached → WorkflowConcurrencyLimitError, nothing written

    Returns:
        The slot's id, for its renewal and release; None if no slot was taken
    """
    if MODE.IS_STANDALONE or user_id is None:
        return None

    limit = _get_concurrency_limit(user_id)
    if limit is None:
        return None

    try:
        with session_scope() as session:
            session.execute(select(User.id).where(User.id == user_id).with_for_update())
            active = session.execute(
                select(func.count())
                .select_from(WorkflowRunSlot)
                .where(WorkflowRunSlot.user_id == user_id, _slot_is_live())
            ).scalar_one()
            if active >= limit.limit:
                slot_id = None
            else:
                slot_id = session.execute(
                    insert(WorkflowRunSlot).values(
                        user_id=user_id,
                        workspace_id=str(workspace_id),
                        unique_id=str(unique_id),
                    )
                ).inserted_primary_key[0]
    except Exception as e:
        logger.error(
            f"Failed to take a workflow slot for user {user_id}: {e}; "
            "starting uncounted",
            exc_info=True,
        )
        return None

    if slot_id is None:
        logger.info(
            f"Workflow start refused for user {user_id}: {active} running, "
            f"{limit.plan_name} plan allows {limit.limit}"
        )
        raise WorkflowConcurrencyLimitError(limit, active)

    _delete_stale_slots(user_id)
    return slot_id


def _delete_stale_slots(user_id: int) -> None:
    """Housekeeping: drop the user's dead runs' slots, by primary key."""
    try:
        with session_scope() as session:
            stale_ids = (
                session.execute(
                    select(WorkflowRunSlot.id).where(
                        WorkflowRunSlot.user_id == user_id, not_(_slot_is_live())
                    )
                )
                .scalars()
                .all()
            )
            if stale_ids:
                session.execute(
                    delete(WorkflowRunSlot).where(WorkflowRunSlot.id.in_(stale_ids))
                )
    except Exception as e:
        logger.warning(f"Failed to delete stale workflow slots of {user_id}: {e}")


def renew_workflow_slot(slot_id: Optional[int]) -> None:
    """
    Mark the slot's run alive.

    Errors are logged, not raised: a missed renewal can only let a later start
    be admitted early.
    """
    if slot_id is None:
        return
    try:
        with session_scope() as session:
            session.execute(
                update(WorkflowRunSlot)
                .where(WorkflowRunSlot.id == slot_id)
                .values(heartbeat_at=func.now())
            )
    except Exception as e:
        logger.warning(f"Failed to renew workflow slot {slot_id}: {e}")


def release_workflow_slot(slot_id: Optional[int]) -> None:
    """
    Delete the slot of a run that ended or will not be scheduled.

    Errors are logged, not raised: a slot left behind stops counting after
    the TTL.
    """
    if slot_id is None:
        return
    try:
        with session_scope() as session:
            session.execute(
                delete(WorkflowRunSlot).where(WorkflowRunSlot.id == slot_id)
            )
    except Exception as e:
        logger.error(f"Failed to release workflow slot {slot_id}: {e}")
