"""
Backstop sweep: release dangling premium assignments after expiration.

Runs periodically as a safety net for the event-driven path. Normally a
premium compute assignment is released when Stripe sends
``customer.subscription.deleted`` (handled in ``WebhookService``). If that
event is missed or never sent (e.g. a lost webhook, or a local expiration
applied by a direct DB update), the per-user EC2/ALB assignment can dangle,
still attached to a user who is now effectively free.

This job finds users whose PREMIUM subscription expired more than
``GRACE_PERIOD_DAYS`` ago, who have no currently-active subscription, and who
still hold an ``active`` (non-standby) premium assignment, and hard-releases
them. It complements (and is more precise than) the activity-based stale
sweep performed by the Premium Cleanup Lambda.
"""

from datetime import timedelta
from typing import List, Tuple

from sqlalchemy import and_, exists
from sqlalchemy.orm import aliased

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.premium.premium_assignment_service import (
    premium_assignment_service,
)
from studio.app.common.core.subscription.constants import (
    PremiumExpirationSweep,
    SubscriptionPeriods,
    SubscriptionPlanIds,
)
from studio.app.common.core.utils.datetime_utils import get_current_datetime
from studio.app.common.db.database import session_scope
from studio.app.common.models.premium_user import PremiumUserAssignment
from studio.app.common.models.subscription import UserSubscription
from studio.app.common.models.user import User

logger = AppLogger.get_logger()


class PremiumExpirationSweepJob:
    @classmethod
    async def run(cls):
        """Periodic backstop: release premium assignments for expired users."""
        logger.info("Starting premium expiration sweep job")

        try:
            candidates = cls._find_dangling_assignments()
        except Exception as e:
            logger.error(
                f"Premium expiration sweep: failed to query candidates: {e}",
                exc_info=True,
            )
            return

        if not candidates:
            logger.debug("Premium expiration sweep: no dangling assignments found")
            return

        logger.info(
            f"Premium expiration sweep: {len(candidates)} dangling "
            f"assignment(s) to release"
        )

        processed = 0
        errors = 0
        for user_id, user_uid in candidates:
            try:
                result = await premium_assignment_service.release_premium_user(
                    user_id=user_id, user_uid=user_uid, hard=True
                )
                if result.get("success"):
                    processed += 1
                    logger.info(
                        f"Premium expiration sweep: released user {user_id} "
                        f"({result.get('message')})"
                    )
                else:
                    errors += 1
                    logger.warning(
                        "Premium expiration sweep: release returned failure for "
                        f"user {user_id}: {result.get('message')}"
                    )
            except Exception as e:
                errors += 1
                logger.error(
                    f"Premium expiration sweep: error releasing user {user_id}: {e}",
                    exc_info=True,
                )

        logger.info(
            f"Premium expiration sweep completed: {processed} released, "
            f"{errors} errors"
        )

    @staticmethod
    def _find_dangling_assignments() -> List[Tuple[int, str]]:
        """
        Return ``[(user_id, user_uid), ...]`` for users who:
        - hold an ``active`` non-standby premium assignment,
        - have a PREMIUM subscription expired more than GRACE_PERIOD_DAYS ago, and
        - have no currently-active subscription (i.e. have not re-subscribed).

        Primitives are extracted inside the session so it can close before the
        async release calls (which talk to AWS Lambda) run.
        """
        current_time = get_current_datetime()
        grace_cutoff = current_time - timedelta(
            days=SubscriptionPeriods.GRACE_PERIOD_DAYS
        )

        # Exclude users who have any currently-active subscription row.
        active_sub = aliased(UserSubscription)
        has_active_sub = exists().where(
            and_(
                active_sub.user_id == UserSubscription.user_id,
                active_sub.expiration > current_time,
            )
        )

        with session_scope() as db:
            rows = (
                db.query(PremiumUserAssignment.user_id, User.uid)
                .join(User, User.id == PremiumUserAssignment.user_id)
                .join(
                    UserSubscription,
                    UserSubscription.user_id == PremiumUserAssignment.user_id,
                )
                .filter(
                    PremiumUserAssignment.status == "active",
                    PremiumUserAssignment.is_standby == False,  # noqa: E712
                    UserSubscription.plan_id == SubscriptionPlanIds.PREMIUM,
                    UserSubscription.expiration <= grace_cutoff,
                    ~has_active_sub,
                )
                .limit(PremiumExpirationSweep.MAX_RELEASES_PER_RUN)
                .all()
            )

            seen = set()
            candidates: List[Tuple[int, str]] = []
            for user_id, user_uid in rows:
                if user_id in seen:
                    continue
                seen.add(user_id)
                candidates.append((user_id, user_uid))
            return candidates
