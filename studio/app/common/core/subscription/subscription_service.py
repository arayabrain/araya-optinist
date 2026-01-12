from datetime import datetime, timezone
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
            .all()
        )

    @staticmethod
    def get_plan_by_tier(
        db: Session, tier: str, active_only: bool = True
    ) -> Optional[SubscriptionPlans]:
        """
        Get a subscription plan by tier (e.g., 'free', 'premium', 'enterprise').

        This enables dynamic plan lookup without hardcoded IDs.

        Args:
            db: Database session
            tier: Plan tier identifier ('free', 'premium', 'enterprise', etc.)
            active_only: If True, only return active plans

        Returns:
            SubscriptionPlans object or None if not found
        """
        query = db.query(SubscriptionPlans).filter(SubscriptionPlans.tier == tier)

        if active_only:
            query = query.filter(SubscriptionPlans.status == SubscriptionStatusType.ACTIVE)

        # If multiple plans with same tier exist, return the one with lowest price
        return query.order_by(SubscriptionPlans.price.asc()).first()

    @staticmethod
    def get_free_plan_id(db: Session) -> Optional[int]:
        """
        Get the ID of the free plan dynamically.

        Returns:
            Plan ID of the free tier plan, or None if not found
        """
        free_plan = __class__.get_plan_by_tier(db, "free")
        return free_plan.id if free_plan else None

    @staticmethod
    def get_default_plan_id(db: Session) -> int:
        """
        Get the default plan ID (free tier) for new users.

        Returns:
            Plan ID of the free tier plan

        Raises:
            HTTPException if no free plan is found
        """
        plan_id = __class__.get_free_plan_id(db)
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

        Args:
            db: Database session
            user_id: The user's ID

        Returns:
            bool: True if the user has an active cancelled subscription, False otherwise
        """
        # Get the user's current active subscription (not expired)
        active_subscription = (
            db.query(UserSubscription)
            .filter(
                UserSubscription.user_id == user_id,
                UserSubscription.expiration > __class__.get_current_datetime(),
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
    def get_subscription_status(
        db: Session, plan_data_id: int, is_cancelled: bool
    ) -> int:
        """
        Determine subscription status based on plan tier and cancellation state.

        This method uses data-driven approach by querying the plan's tier from
        the database instead of hardcoded plan ID checks, enabling flexible
        plan management without code changes.

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

        # Query plan tier from database (data-driven approach)
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

        # Use tier-based logic instead of hardcoded plan IDs
        if plan.tier == "free":
            return SubscriptionUserStatus.FREE
        elif plan.is_premium_tier:
            # Covers premium, enterprise, professional, and any future premium tiers
            return SubscriptionUserStatus.SUBSCRIBED
        else:
            # Unknown tier, default to FREE for safety
            logger.warning(
                f"Unknown plan tier '{plan.tier}' for plan ID {plan_data_id}, "
                f"defaulting to FREE status"
            )
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
        Get the current UTC date and time
        """
        try:
            return datetime.now(timezone.utc)
        except Exception as e:
            logger.error(f"Error getting current datetime: {str(e)}")
            return None

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
