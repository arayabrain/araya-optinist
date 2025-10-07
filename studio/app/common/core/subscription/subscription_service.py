import os
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Tuple

from sqlalchemy import and_
from sqlmodel import Session

from studio.app.common import models as common_model
from studio.app.common.core.logger import AppLogger
from studio.app.common.models.subscription import (
    SubscriptionCancellation,
    SubscriptionPlans,
    SubscriptionUserPurchase,
    SyncStatus,
    UserSubscription,
)

logger = AppLogger.get_logger()


class SubscriptionUserStatus(Enum):
    FREE = 1
    SUBSCRIBED = 2
    EXPIRED = 3
    CANCELED = 4


class SubscriptionStatusType(Enum):
    ACTIVE = "1"
    INACTIVE = "0"


class SubscriptionCurrencyType(Enum):
    USD = 1
    JPY = 2

    def get_currency_string(self):
        """Get the string representation of the currency"""
        if self == __class__.USD:
            return "usd"
        elif self == __class__.JPY:
            return "jpy"
        return None

    def get_currency_enum(value: str):
        """Get the enum representation of the currency"""
        if value == "usd":
            return __class__.USD
        elif value == "jpy":
            return __class__.JPY
        return None


class SubscriptionService:
    @staticmethod
    def get_active_plans(db: Session) -> List[SubscriptionPlans]:
        return (
            db.query(SubscriptionPlans)
            .filter(SubscriptionPlans.status == SubscriptionStatusType.ACTIVE.value)
            .all()
        )

    @staticmethod
    def is_subscription_cancelled(db: Session, user_id: int) -> bool:
        """
        Check if a user's current active subscription is cancelled.

        Args:
            db: Database session
            user_id: The user's ID

        Returns:
            bool: True if the user has an active cancelled subscription, False otherwise
        """
        from datetime import datetime

        # Get the user's current active subscription (not expired)
        active_subscription = (
            db.query(UserSubscription)
            .filter(
                UserSubscription.user_id == user_id,
                UserSubscription.expiration > datetime.utcnow(),
            )
            .order_by(UserSubscription.expiration.desc())
            .first()
        )

        logger.info(f"Active subscription for user {user_id}: {active_subscription}")

        # If no active subscription, it's not cancelled (it's expired or doesn't exist)
        if not active_subscription:
            return False

        # Find the most recent purchase that matches or came before this subscription
        latest_purchase = (
            db.query(SubscriptionUserPurchase)
            .filter(
                SubscriptionUserPurchase.user_id == user_id,
                SubscriptionUserPurchase.plan_id == active_subscription.plan_id,
                SubscriptionUserPurchase.created_at <= active_subscription.created_at,
            )
            .order_by(SubscriptionUserPurchase.created_at.desc())
            .first()
        )

        logger.info(f"Latest purchase for user {user_id}: {latest_purchase}")

        if not latest_purchase:
            return False

        # Check if this purchase has a cancellation record
        cancellation = (
            db.query(SubscriptionCancellation)
            .filter(SubscriptionCancellation.purchases_id == latest_purchase.id)
            .first()
        )

        logger.info(f"Cancellation record for user {user_id}: {cancellation}")

        return cancellation is not None

    @staticmethod
    def get_subscription_status(plan_data_id: int, is_cancelled: bool) -> int:
        # Determine status based on plan ID and cancellation state
        if is_cancelled:
            subscription_status = SubscriptionUserStatus.CANCELED.value
        elif plan_data_id == 1:
            subscription_status = SubscriptionUserStatus.FREE.value
        elif plan_data_id >= 2:
            subscription_status = SubscriptionUserStatus.SUBSCRIBED.value
        else:
            subscription_status = SubscriptionUserStatus.FREE.value
        return subscription_status

    @staticmethod
    def get_plan_by_id(db: Session, plan_id: int) -> SubscriptionPlans:
        return (
            db.query(SubscriptionPlans)
            .filter(SubscriptionPlans.id == plan_id, SubscriptionPlans.status.is_(True))
            .first()
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
                    common_model.UserSubscription.expiration
                    > __class__.get_current_datetime(),
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
                common_model.UserSubscription.expiration
                <= __class__.get_current_datetime(),
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
                    UserSubscription.expiration > __class__.get_current_datetime(),
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
    def get_current_datetime() -> datetime:
        """
        Get the current UTC date and time
        """
        try:
            return datetime.now(timezone.utc)
        except Exception as e:
            logger.error(f"Error getting current datetime: {str(e)}")
            return None


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
            subscription.last_synced = SubscriptionService.get_current_datetime()
            subscription.updated_at = SubscriptionService.get_current_datetime()
            db.commit()

            logger.info(f"Successfully synced subscription {subscription_user_id}")
            return True

        except Exception as e:
            logger.error(f"Error syncing subscription {subscription_user_id}: {str(e)}")

            # Mark as failed
            if subscription:
                subscription.sync_status = SyncStatus.FAILED
                subscription.updated_at = SubscriptionService.get_current_datetime()
                db.commit()

            return False
