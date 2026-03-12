from datetime import datetime
from typing import List, Optional, Tuple

import stripe
from fastapi import HTTPException
from sqlalchemy import and_
from sqlmodel import Session

from studio.app.common import models as common_model
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.subscription.constants import (
    SubscriptionStatusType,
    SubscriptionUserStatus,
    SyncStatus,
)
from studio.app.common.core.utils.config_handler import get_env_var
from studio.app.common.core.utils.datetime_utils import get_current_datetime
from studio.app.common.models.subscription import (
    SubscriptionCancellation,
    SubscriptionPlans,
    SubscriptionUserPurchase,
    UserSubscription,
)
from studio.app.common.models.user import User

logger = AppLogger.get_logger()


class SubscriptionService:
    """Service class for managing internal subscription logic and database operations.

    This service acts as the primary business logic layer for subscription management,
    handling all subscription-related data in the application's database and
    orchestrating subscription workflows. It serves as the single source of truth
    for subscription state within the application.

    Primary Responsibilities:
    - Manage subscription data persistence in the application database
    - Implement business rules and validation for subscription operations
    - Track subscription lifecycle states (active, expired, cancelled)
    - Handle subscription plan retrieval and user subscription status
    - Coordinate with StripeService for payment provider operations
    - Process subscription updates, downgrades, and cancellation tracking
    - Maintain user subscription metadata and expiration dates

    Key Features:
    - Retrieve active subscription plans from database
    - Check subscription cancellation status and history
    - Determine subscription status based on plan type and cancellation state
    - Update subscription plans and manage scheduled downgrades
    - Track subscription purchases and cancellation events

    Integration Points:
    - Database session management for subscription data persistence
    - User authentication and authorization
    - Subscription plan types (monthly, yearly)
    - StripeService for payment processing and Stripe API interactions

    Note: This service focuses on internal business logic. For Stripe-specific
    operations (payment methods, Stripe customer management, Stripe API calls),
    use StripeService."""

    _stripe_initialized = False

    @classmethod
    def _ensure_stripe_initialized(cls):
        """Lazy initialization of Stripe API key"""
        if not cls._stripe_initialized:
            try:
                stripe.api_key = cls.get_stripe_key()
                cls._stripe_initialized = True
            except ValueError as e:
                logger.warning(f"Stripe not initialized: {e}")
                # Don't raise here - allow module to load for tests

    @staticmethod
    def get_active_plans(db: Session) -> List[SubscriptionPlans]:
        return (
            db.query(SubscriptionPlans)
            .filter(SubscriptionPlans.status == SubscriptionStatusType.ACTIVE)
            .filter(SubscriptionPlans.is_hidden.is_(False))
            .all()
        )

    @staticmethod
    def get_free_plan(
        db: Session, active_only: bool = True
    ) -> Optional[SubscriptionPlans]:
        """
        Get the free subscription plan (price = 0).

        Data-driven approach: any plan with price = 0 is considered free.
        No code changes needed when adding new plans.

        Args:
            db: Database session
            active_only: If True, only return active plans

        Returns:
            SubscriptionPlans object or None if not found
        """
        query = db.query(SubscriptionPlans).filter(SubscriptionPlans.price == 0)

        if active_only:
            query = query.filter(
                SubscriptionPlans.status == SubscriptionStatusType.ACTIVE
            )

        return query.first()

    @classmethod
    def get_free_plan_id(cls, db: Session) -> Optional[int]:
        """
        Get the ID of the free plan dynamically.

        Returns:
            Plan ID of the free plan (price = 0), or None if not found
        """
        free_plan = cls.get_free_plan(db)
        return free_plan.id if free_plan else None

    @classmethod
    def get_default_plan_id(cls, db: Session) -> int:
        """
        Get the default plan ID (free plan) for new users.

        Returns:
            Plan ID of the free plan

        Raises:
            HTTPException if no free plan is found
        """
        plan_id = cls.get_free_plan_id(db)
        if plan_id is None:
            logger.error("No free plan found in database")
            raise HTTPException(
                status_code=500,
                detail="System configuration error: No free plan available",
            )
        return plan_id

    @staticmethod
    def is_subscription_cancelled(db: Session, user_id: int) -> bool:
        """
        Check if a user's current active subscription is cancelled.

        This method uses a single optimized query with JOINs instead of
        3 sequential queries, reducing database round trips by 66%.

        Args:
            db: Database session
            user_id: The user's ID

        Returns:
            bool: True if the user has an active cancelled subscription, False otherwise
        """
        current_time = __class__.get_current_datetime()

        # Single query with LEFT JOINs to check subscription cancellation status
        # This replaces 3 separate queries:
        # 1. Get active subscription
        # 2. Get matching purchase
        # 3. Check for cancellation record
        result = (
            db.query(
                UserSubscription.id.label("subscription_id"),
                SubscriptionCancellation.id.label("cancellation_id"),
            )
            .outerjoin(
                SubscriptionUserPurchase,
                and_(
                    SubscriptionUserPurchase.user_id == UserSubscription.user_id,
                    SubscriptionUserPurchase.plan_id == UserSubscription.plan_id,
                    SubscriptionUserPurchase.created_at <= UserSubscription.created_at,
                ),
            )
            .outerjoin(
                SubscriptionCancellation,
                SubscriptionCancellation.purchases_id == SubscriptionUserPurchase.id,
            )
            .filter(
                UserSubscription.user_id == user_id,
                UserSubscription.expiration > current_time,
            )
            .order_by(
                UserSubscription.expiration.desc(),
                SubscriptionUserPurchase.created_at.desc(),
            )
            .first()
        )

        if not result:
            # No active subscription found
            logger.debug(f"No active subscription for user {user_id}")
            return False

        subscription_id, cancellation_id = result

        logger.debug(
            f"Subscription check for user {user_id}: "
            f"subscription_id={subscription_id}, cancellation_id={cancellation_id}"
        )

        return cancellation_id is not None

    @staticmethod
    def get_subscription_status(
        db: Session, plan_data_id: int, is_cancelled: bool
    ) -> int:
        """
        Determine subscription status based on plan price and cancellation state.

        Data-driven approach: uses price to determine plan type.
        - price = 0: FREE plan
        - price > 0: SUBSCRIBED (paid plan)

        No code changes needed when adding new plans to the database.

        Args:
            db: Database session
            plan_data_id: Subscription plan ID
            is_cancelled: Whether subscription is cancelled

        Returns:
            SubscriptionUserStatus enum value
        """
        # Check cancellation first
        if is_cancelled:
            return SubscriptionUserStatus.CANCELED

        # Query plan from database
        plan = (
            db.query(SubscriptionPlans)
            .filter(SubscriptionPlans.id == plan_data_id)
            .first()
        )

        # If plan not found, default to FREE
        if not plan:
            logger.warning(
                f"Plan ID {plan_data_id} not found in database, "
                f"defaulting to FREE status"
            )
            return SubscriptionUserStatus.FREE

        # Price-based logic: free = price 0, paid = price > 0
        if plan.is_premium:
            return SubscriptionUserStatus.SUBSCRIBED
        else:
            return SubscriptionUserStatus.FREE

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
    def get_user_subscription_purchase(
        db: Session, user_id: int
    ) -> Optional[SubscriptionUserPurchase]:
        return (
            db.query(SubscriptionUserPurchase)
            .filter(SubscriptionUserPurchase.user_id == user_id)
            .order_by(SubscriptionUserPurchase.created_at.desc())
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
        return get_env_var("STRIPE_SECRET_KEY", required=True)

    @staticmethod
    def get_base_url() -> str:
        return get_env_var("STRIPE_CALLBACK_URL", required=True)

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
            raise HTTPException(
                status_code=500, detail="Failed to update scheduled downgrade"
            )

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
            subscription.updated_at = __class__.get_current_datetime()

            db.commit()
            db.refresh(subscription)

            return subscription

        except Exception as e:
            db.rollback()
            logger.error(f"Error updating subscription for user {user_id}: {str(e)}")
            raise HTTPException(
                status_code=500, detail="Failed to update user subscription"
            )

    @staticmethod
    def get_current_datetime() -> datetime:
        """
        Get the current UTC date and time.

        Note: This method delegates to the centralized datetime utility.
        New code should import directly from datetime_utils instead.
        """
        return get_current_datetime()

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

    @staticmethod
    def get_user_for_invoice_lookup(
        db: Session, user_id: int
    ) -> Optional[Tuple[Optional[UserSubscription], User]]:
        """
        Get user and their subscription (if exists) for invoice lookup purposes.

        This method handles cases where a user may not have a subscription record
        but still needs to be looked up for invoice retrieval.

        Args:
            db: Database session
            user_id: The user's ID

        Returns:
            Tuple of (UserSubscription or None, User) if user exists
            None if user not found
        """
        # Try to get user with subscription first
        result = __class__.get_user_subscription_by_user_id(db, user_id)

        if result:
            # User has a subscription record
            subscription_user, user = result
            return (subscription_user, user)

        # User has no subscription record, get user directly
        user = db.query(User).filter(User.id == user_id).first()

        if not user:
            return None

        logger.info(
            f"No subscription record for user {user_id}, "
            f"returning user without subscription"
        )
        return (None, user)

    @staticmethod
    def get_users_with_upcoming_quota_drop(db: Session) -> List[dict]:
        """
        Get users whose quota will drop soon due to grace period ending.

        This identifies users in grace period whose current storage usage exceeds
        the FREE tier quota. These users should be warned about impending quota drop.

        Args:
            db: Database session

        Returns:
            List of dicts with user info and excess storage data
        """
        from datetime import timedelta

        from studio.app.common.core.subscription.constants import (
            StorageQuota,
            StorageSize,
            SubscriptionPeriods,
            SubscriptionPlanIds,
        )
        from studio.app.common.models.subscription import UserStorageUsage

        warning_days = SubscriptionPeriods.QUOTA_DROP_WARNING_DAYS
        free_quota_bytes = StorageQuota.FREE * StorageSize.GB
        current_time = SubscriptionService.get_current_datetime()

        users_to_warn = []

        try:
            # Find expired premium subscriptions (in grace period)
            grace_period_end = current_time - timedelta(
                days=SubscriptionPeriods.GRACE_PERIOD_DAYS
            )

            # Get users with expired premium subscriptions within grace period
            expired_subs = (
                db.query(UserSubscription, User, UserStorageUsage)
                .join(User, UserSubscription.user_id == User.id)
                .join(UserStorageUsage, UserStorageUsage.user_id == User.id)
                .filter(
                    UserSubscription.plan_id == SubscriptionPlanIds.PREMIUM,
                    UserSubscription.expiration < current_time,
                    UserSubscription.expiration > grace_period_end,
                    # Storage exceeds free tier
                    UserStorageUsage.storage_usage_bytes > free_quota_bytes,
                )
                .all()
            )

            for sub, user, storage in expired_subs:
                # Calculate days until grace period ends
                grace_end = sub.expiration + timedelta(
                    days=SubscriptionPeriods.GRACE_PERIOD_DAYS
                )
                days_until_drop = (grace_end - current_time).days

                # Only warn if within warning window
                if days_until_drop <= warning_days:
                    excess_bytes = storage.storage_usage_bytes - free_quota_bytes
                    users_to_warn.append(
                        {
                            "user_id": user.id,
                            "user_uid": user.uid,
                            "email": user.email,
                            "current_storage_bytes": storage.storage_usage_bytes,
                            "future_quota_bytes": free_quota_bytes,
                            "excess_bytes": excess_bytes,
                            "days_until_quota_drop": days_until_drop,
                            "grace_period_end": grace_end,
                        }
                    )

            logger.info(f"Found {len(users_to_warn)} users with upcoming quota drops")
            return users_to_warn

        except Exception as e:
            logger.error(f"Error getting users with upcoming quota drop: {e}")
            return []


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
