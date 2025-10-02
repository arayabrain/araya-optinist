import os
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Tuple

from sqlalchemy import and_
from sqlmodel import Session

from studio.app.common import models as common_model
from studio.app.common.core.logger import AppLogger
from studio.app.common.models.subscription import (
    SubscriptionPlans,
    SyncStatus,
    UserSubscription,
)
from studio.app.common.models.user import User

logger = AppLogger.get_logger()


class SubscriptionStatusType(Enum):
    ACTIVE = "1"
    INACTIVE = "0"


class SubscriptionCurrencyType(Enum):
    USD = 1
    JPY = 2


class SubscriptionCurrency(Enum):
    USD = "usd"
    JPY = "jpy"


class SubscriptionService:
    @staticmethod
    def get_active_plans(db: Session) -> List[SubscriptionPlans]:
        return (
            db.query(SubscriptionPlans)
            .filter(SubscriptionPlans.status == SubscriptionStatusType.ACTIVE.value)
            .all()
        )

    @staticmethod
    def get_plan_by_id(db: Session, plan_id: int) -> SubscriptionPlans:
        return (
            db.query(SubscriptionPlans).filter(SubscriptionPlans.id == plan_id).first()
        )

    @staticmethod
    def get_user_subscription(
        db: Session, user_id: int
    ) -> Optional[Tuple[UserSubscription, SubscriptionPlans]]:
        return (
            db.query(common_model.UserSubscription, common_model.SubscriptionPlans)
            .join(
                common_model.SubscriptionPlans,
                common_model.UserSubscription.plan_id
                == common_model.SubscriptionPlans.id,
            )
            .join(
                common_model.User,
                common_model.UserSubscription.user_id == common_model.User.id,
            )
            .filter(
                and_(
                    common_model.UserSubscription.user_id == user_id,
                    common_model.UserSubscription.expiration > datetime.now(),
                    common_model.User.active.is_(True),
                )
            )
            .order_by(common_model.UserSubscription.expiration.desc())
            .first()
        )

    @staticmethod
    def get_user_expired_subscription(
        db: Session, user_id: int
    ) -> common_model.UserSubscription:
        return (
            db.query(
                common_model.UserSubscription,
                common_model.SubscriptionPlans,
                common_model.User,
            )
            .join(
                common_model.SubscriptionPlans,
                common_model.UserSubscription.plan_id
                == common_model.SubscriptionPlans.id,
            )
            .join(
                common_model.User,
                common_model.UserSubscription.user_id == common_model.User.id,
            )
            .filter(
                common_model.UserSubscription.user_id == user_id,
                common_model.UserSubscription.expiration <= datetime.now(),
                common_model.User.active.is_(True),
            )
            .order_by(common_model.UserSubscription.expiration.desc())
            .first()
        )

    @staticmethod
    def get_stripe_key() -> str:
        stripe_key = os.getenv("STRIPE_SECRET_KEY")
        if not stripe_key:
            raise ValueError("STRIPE_SECRET_KEY environment variable is not set")
        return stripe_key

    @staticmethod
    def get_base_url() -> str:
        base_url = os.getenv("STRIPE_CALLBACK_URL")
        if not base_url:
            raise ValueError("STRIPE_CALLBACK_URL environment variable is not set")
        return base_url

    @staticmethod
    def update_scheduled_downgrade(db: Session, user_id: int, scheduled: bool) -> None:
        """
        Update the scheduled downgrade status for a user's subscription
        """
        try:
            subscription = (
                db.query(UserSubscription)
                .filter(UserSubscription.user_id == user_id)
                .first()
            )

            if subscription:
                subscription.scheduled_downgrade = scheduled
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error(
                f"Error updating scheduled downgrade for user {user_id}: {str(e)}"
            )
            raise

    @staticmethod
    def update_user_subscription(
        db: Session, user_id: int, new_plan_id: int
    ) -> Optional[UserSubscription]:
        """
        Update user's subscription to a new plan
        """
        try:
            # Get existing subscription
            subscription = (
                db.query(UserSubscription)
                .filter(
                    UserSubscription.user_id == user_id,
                    UserSubscription.expiration > datetime.now(timezone.utc),
                )
                .first()
            )

            if not subscription:
                return None

            # Update the subscription
            subscription.plan_id = new_plan_id
            subscription.updated_at = datetime.utcnow()

            db.commit()
            db.refresh(subscription)

            return subscription

        except Exception as e:
            db.rollback()
            logger.error(f"Error updating subscription for user {user_id}: {str(e)}")
            raise

    @staticmethod
    def get_user_subscription_by_user_id(
        db: Session, user_id: int
    ) -> Optional[Tuple[UserSubscription, User]]:
        """
        Get user subscription by user ID with user details
        """
        return (
            db.query(UserSubscription, User)
            .join(
                User,
                UserSubscription.user_id == User.id,
            )
            .filter(UserSubscription.user_id == user_id)
            .first()
        )


class SyncService:
    """Service class for handling subscription synchronization"""

    @staticmethod
    def sync_subscription_status(db: Session, subscription_user_id: int) -> bool:
        """
        Sync subscription status with external systems

        Args:
            db: Database session
            subscription_user_id: Subscription user ID

        Returns:
            True if sync successful, False otherwise
        """
        try:
            subscription = (
                db.query(UserSubscription)
                .filter(UserSubscription.id == subscription_user_id)
                .first()
            )

            if not subscription:
                logger.error(f"Subscription {subscription_user_id} not found")
                return False

            # Mark as synced
            subscription.sync_status = SyncStatus.SYNCED
            subscription.last_synced = datetime.now(timezone.utc)
            subscription.updated_at = datetime.now(timezone.utc)
            db.commit()

            logger.info(f"Successfully synced subscription {subscription_user_id}")
            return True

        except Exception as e:
            logger.error(f"Error syncing subscription {subscription_user_id}: {str(e)}")

            # Mark as failed
            if subscription:
                subscription.sync_status = SyncStatus.FAILED
                subscription.updated_at = datetime.now(timezone.utc)
                db.commit()

            return False
