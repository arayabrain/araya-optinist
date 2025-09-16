from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import stripe
from fastapi import logger
from sqlmodel import Enum, Session

from studio.app.common.models.subscription import (
    SubscriptionPlans,
    SubscriptionProvider,
    SubscriptionUserAccount,
    SubscriptionUserPurchase,
    UserSubscription,
)


class SUBSCRIPTION_ACTIVE_STATUS(Enum):
    ACTIVE = "1"
    INACTIVE = "0"


class PaymentService:
    """Service class for handling checkout operations"""

    @staticmethod
    def get_existing_subscription(
        db: Session, user_id: int
    ) -> Optional[UserSubscription]:
        """
        Check if user has an active subscription

        Args:
            db: Database session
            user_id: Internal user ID

        Returns:
            UserSubscription object if active subscription exists, else None
        """
        return (
            db.query(UserSubscription)
            .filter(
                UserSubscription.user_id == user_id,
                UserSubscription.expiration > datetime.now(timezone.utc),
            )
            .first()
        )

    @staticmethod
    def verify_stripe_session(session_id: str) -> Dict[str, Any]:
        """
        Verify and retrieve Stripe checkout session details

        Args:
            session_id: Stripe checkout session ID

        Returns:
            Dict containing session data

        Raises:
            stripe.error.StripeError: If session is invalid or API error occurs
        """
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            return {
                "customer_id": session.customer,
                "payment_status": session.payment_status,
                "amount_total": session.amount_total,
                "currency": session.currency,
                "metadata": session.metadata or {},
            }
        except stripe.error.StripeError as e:
            logger.error(f"Stripe API error: {str(e)}")
            raise

    @staticmethod
    def get_or_create_stripe_provider(db: Session) -> int:
        """
        Get existing Stripe provider or create new one

        Args:
            db: Database session

        Returns:
            Provider ID for Stripe
        """
        provider = (
            db.query(SubscriptionProvider)
            .filter(SubscriptionProvider.name == "stripe")
            .first()
        )

        if not provider:
            provider = SubscriptionProvider(name="stripe")
            db.add(provider)
            db.commit()
            db.refresh(provider)

        return provider.id

    @staticmethod
    def get_subscription_plan(db: Session, plan_id: int) -> Optional[SubscriptionPlans]:
        """
        Get active subscription plan by ID

        Args:
            db: Database session
            plan_id: Subscription plan ID

        Returns:
            SubscriptionPlan object or None if not found
        """
        return (
            db.query(SubscriptionPlans)
            .filter(
                SubscriptionPlans.id == plan_id,
                SubscriptionPlans.status == SUBSCRIPTION_ACTIVE_STATUS.ACTIVE.value,
            )
            .first()
        )

    @staticmethod
    def calculate_expiration_date(billing_cycle: int) -> datetime:
        """
        Calculate subscription expiration date based on billing cycle

        Args:
            billing_cycle: Billing cycle in months

        Returns:
            Expiration datetime
        """
        return datetime.now(timezone.utc) + timedelta(days=30 * billing_cycle)

    @staticmethod
    def create_or_update_user_account(
        db: Session, user_id: int, provider_id: int, customer_id: str
    ) -> SubscriptionUserAccount:
        """
        Create or update user account with payment provider

        Args:
            db: Database session
            user_id: Internal user ID
            provider_id: Payment provider ID
            customer_id: Provider's customer ID

        Returns:
            SubscriptionUserAccount object
        """
        user_account = (
            db.query(SubscriptionUserAccount)
            .filter(
                SubscriptionUserAccount.user_id == user_id,
                SubscriptionUserAccount.provider_id == provider_id,
            )
            .first()
        )

        if not user_account:
            user_account = SubscriptionUserAccount(
                user_id=user_id,
                provider_id=provider_id,
                provider_customer_id=customer_id,
            )
            db.add(user_account)
        else:
            user_account.provider_customer_id = customer_id
            user_account.updated_at = datetime.now(timezone.utc)

        return user_account

    @staticmethod
    def create_or_update_subscription(
        db: Session, user_id: int, plan_id: int, expiration_date: datetime
    ) -> int:
        """
        Create new subscription or update existing one

        Args:
            db: Database session
            user_id: Internal user ID
            plan_id: Subscription plan ID
            expiration_date: When subscription expires

        Returns:
            Subscription user ID
        """
        # Check for existing active subscription
        existing_subscription = (
            db.query(UserSubscription)
            .filter(
                UserSubscription.user_id == user_id,
                UserSubscription.expiration > datetime.now(timezone.utc),
            )
            .first()
        )

        if existing_subscription:
            # Update existing subscription
            existing_subscription.plan_id = plan_id
            existing_subscription.expiration = expiration_date
            existing_subscription.updated_at = datetime.now(timezone.utc)
            return existing_subscription.id
        else:
            # Create new subscription
            new_subscription = UserSubscription(
                plan_id=plan_id, user_id=user_id, expiration=expiration_date
            )
            db.add(new_subscription)
            db.flush()  # Get ID without committing
            return new_subscription.id

    @staticmethod
    def record_purchase(
        db: Session, plan_id: int, user_id: int
    ) -> SubscriptionUserPurchase:
        """
        Record a new purchase in purchase history

        Args:
            db: Database session
            plan_id: Subscription plan ID
            user_id: Internal user ID

        Returns:
            SubscriptionUserPurchase object
        """
        purchase = SubscriptionUserPurchase(plan_id=plan_id, user_id=user_id)
        db.add(purchase)
        db.flush()  # Get ID without committing
        return purchase

    @staticmethod
    def process_payment_success(
        db: Session, session_id: str, user_id: int, plan_id: int
    ) -> Dict[str, Any]:
        """
        Process successful checkout completion

        Args:
            db: Database session
            session_id: Stripe checkout session ID
            user_id: Internal user ID
            plan_id: Subscription plan ID

        Returns:
            Dict with processing results

        Raises:
            ValueError: If validation fails
            Exception: For processing errors
        """
        try:
            # 1. CHECK FOR DUPLICATE PROCESSING FIRST
            # Check if this session has already been processed
            existing_purchase = (
                db.query(SubscriptionUserPurchase)
                .join(
                    UserSubscription,
                    SubscriptionUserPurchase.user_id == UserSubscription.user_id,
                )
                .filter(
                    SubscriptionUserPurchase.user_id == user_id,
                    SubscriptionUserPurchase.plan_id == plan_id,
                    SubscriptionUserPurchase.created_at
                    > datetime.now(timezone.utc)
                    - timedelta(minutes=30),  # Within last 30 minutes
                )
                .first()
            )

            if existing_purchase:
                # Find the corresponding subscription
                existing_subscription = (
                    db.query(UserSubscription)
                    .filter(
                        UserSubscription.user_id == user_id,
                        UserSubscription.plan_id == plan_id,
                        UserSubscription.expiration > datetime.now(timezone.utc),
                    )
                    .first()
                )

                logger.info(
                    f"Duplicate processing detected for user {user_id}, "
                    f"session {session_id}"
                )
                return {
                    "success": True,
                    "subscription_user_id": (
                        existing_subscription.id if existing_subscription else None
                    ),
                    "purchase_id": existing_purchase.id,
                    "expiration_date": (
                        existing_subscription.expiration
                        if existing_subscription
                        else None
                    ),
                    "message": "Subscription already processed successfully",
                }

            # 2. Verify Stripe session
            session_data = PaymentService.verify_stripe_session(session_id)

            if session_data["payment_status"] != "paid":
                raise ValueError("Payment not completed")

            # 3. Get subscription plan
            plan = PaymentService.get_subscription_plan(db, plan_id)
            if not plan:
                raise ValueError("Subscription plan not found")

            # 4. Get or create Stripe provider
            stripe_provider_id = PaymentService.get_or_create_stripe_provider(db)

            # 5. Create or update user account
            PaymentService.create_or_update_user_account(
                db, user_id, stripe_provider_id, session_data["customer_id"]
            )

            # 6. Calculate expiration date
            expiration_date = PaymentService.calculate_expiration_date(
                plan.billing_cycle
            )

            # 7. Create or update subscription
            subscription_user_id = PaymentService.create_or_update_subscription(
                db, user_id, plan_id, expiration_date
            )

            # 8. Record purchase
            purchase = PaymentService.record_purchase(db, plan_id, user_id)

            # 9. Commit all changes
            db.commit()

            logger.info(
                f"Successfully processed checkout for user {user_id}, "
                f"plan {plan_id}, session {session_id}"
            )

            return {
                "success": True,
                "subscription_user_id": subscription_user_id,
                "purchase_id": purchase.id,
                "expiration_date": expiration_date,
                "message": "Subscription activated successfully",
            }

        except Exception as e:
            logger.error(f"Error processing checkout success: {str(e)}")
            db.rollback()
            raise
