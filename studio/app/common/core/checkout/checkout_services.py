import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import stripe
from sqlalchemy.orm import Session

# Import your existing models and the enums you'll add
from studio.app.common.models.subscription import (
    CancellationReason,
    SubscriptionCancellation,
    SubscriptionPlans,
    SubscriptionProvider,
    SubscriptionUserAccount,
    SubscriptionUserPurchase,
    SyncStatus,
    UserSubscription,
)

logger = logging.getLogger(__name__)

SUBSCRIPTION_ACTIVE_STATUS = {
    "ACTIVE": "1",
    "INACTIVE": "0",
}


class CheckoutService:
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
                SubscriptionPlans.status == SUBSCRIPTION_ACTIVE_STATUS["ACTIVE"],
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
    def process_checkout_success(
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
            session_data = CheckoutService.verify_stripe_session(session_id)

            if session_data["payment_status"] != "paid":
                raise ValueError("Payment not completed")

            # 3. Get subscription plan
            plan = CheckoutService.get_subscription_plan(db, plan_id)
            if not plan:
                raise ValueError("Subscription plan not found")

            # 4. Get or create Stripe provider
            stripe_provider_id = CheckoutService.get_or_create_stripe_provider(db)

            # 5. Create or update user account
            CheckoutService.create_or_update_user_account(
                db, user_id, stripe_provider_id, session_data["customer_id"]
            )

            # 6. Calculate expiration date
            expiration_date = CheckoutService.calculate_expiration_date(
                plan.billing_cycle
            )

            # 7. Create or update subscription
            subscription_user_id = CheckoutService.create_or_update_subscription(
                db, user_id, plan_id, expiration_date
            )

            # 8. Record purchase
            purchase = CheckoutService.record_purchase(db, plan_id, user_id)

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


class WebhookService:
    """Service class for handling Stripe webhooks"""

    @staticmethod
    def handle_checkout_completed(db: Session, session_data: Dict[str, Any]) -> None:
        """
        Handle checkout.session.completed webhook

        Args:
            db: Database session
            session_data: Webhook session data
        """
        session_id = session_data.get("id")
        logger.info(f"Webhook: Checkout session completed: {session_id}")

        try:
            # Only handle subscription checkouts
            if session_data.get("mode") == "subscription":
                customer_id = session_data.get("customer")
                subscription_id = session_data.get("subscription")

                if not customer_id or not subscription_id:
                    logger.warning(
                        f"Missing customer_id or subscription_id for session "
                        f"{session_id}"
                    )
                    return

                # Get the subscription to access the payment method
                subscription = stripe.Subscription.retrieve(subscription_id)
                payment_method_id = subscription.default_payment_method

                if payment_method_id:

                    # Set this payment method as the customer's default
                    stripe.Customer.modify(
                        customer_id,
                        invoice_settings={"default_payment_method": payment_method_id},
                    )

                    logger.info(
                        f"Successfully set default payment method for customer "
                        f"{customer_id}"
                    )
                else:
                    logger.warning(
                        f"No payment method found for subscription {subscription_id}"
                    )

            # Additional logic can be added here if needed

        except stripe.error.StripeError as e:
            logger.error(
                f"Stripe error while setting default payment method for session "
                f"{session_id}: {str(e)}"
            )
        except Exception as e:
            logger.error(
                f"Error handling checkout completed for session {session_id}: {str(e)}"
            )

    @staticmethod
    def handle_payment_failed(db: Session, invoice_data: Dict[str, Any]) -> None:
        """
        Handle invoice.payment_failed webhook

        Args:
            db: Database session
            invoice_data: Webhook invoice data
        """
        customer_id = invoice_data.get("customer")
        logger.warning(f"Webhook: Payment failed for customer: {customer_id}")

        # Find user account by customer ID
        user_account = (
            db.query(SubscriptionUserAccount)
            .filter(SubscriptionUserAccount.provider_customer_id == customer_id)
            .first()
        )

        if user_account:
            # Find active subscription and mark as failed
            subscription = (
                db.query(UserSubscription)
                .filter(
                    UserSubscription.user_id == user_account.user_id,
                    UserSubscription.expiration > datetime.now(timezone.utc),
                )
                .first()
            )

            if subscription:
                subscription.sync_status = SyncStatus.FAILED
                subscription.updated_at = datetime.now(timezone.utc)
                db.commit()
                logger.info(
                    f"Marked subscription as failed for user {user_account.user_id}"
                )

    @staticmethod
    def handle_subscription_cancelled(
        db: Session, subscription_data: Dict[str, Any]
    ) -> None:
        """
        Handle customer.subscription.deleted webhook

        Args:
            db: Database session
            subscription_data: Webhook subscription data
        """
        customer_id = subscription_data.get("customer")
        stripe_subscription_id = subscription_data.get("id")
        logger.info(f"Webhook: Subscription cancelled: {stripe_subscription_id}")

        # Find user account by customer ID
        user_account = (
            db.query(SubscriptionUserAccount)
            .filter(SubscriptionUserAccount.provider_customer_id == customer_id)
            .first()
        )

        if user_account:
            # Find active subscription and expire it
            subscription = (
                db.query(UserSubscription)
                .filter(
                    UserSubscription.user_id == user_account.user_id,
                    UserSubscription.expiration > datetime.now(timezone.utc),
                )
                .first()
            )

            if subscription:
                # Expire subscription immediately
                subscription.expiration = datetime.now(timezone.utc)
                subscription.updated_at = datetime.now(timezone.utc)

                # Record cancellation
                cancellation = SubscriptionCancellation(
                    cancelled_by_user_id=user_account.user_id,
                    purchases_id=subscription.id,
                    reason=CancellationReason.USER_REQUEST,
                    notes=(
                        f"Cancelled via Stripe webhook for subscription "
                        f"{stripe_subscription_id}"
                    ),
                )
                db.add(cancellation)
                db.commit()

                logger.info(f"Cancelled subscription for user {user_account.user_id}")


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

    @staticmethod
    def get_subscription_status(db: Session, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Get current subscription status for a user

        Args:
            db: Database session
            user_id: Internal user ID

        Returns:
            Dict with subscription details or None if no active subscription
        """
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

        plan = (
            db.query(SubscriptionPlans)
            .filter(SubscriptionPlans.id == subscription.plan_id)
            .first()
        )

        return {
            "subscription_id": subscription.id,
            "plan_id": subscription.plan_id,
            "plan_name": plan.name if plan else "Unknown",
            "expiration": subscription.expiration,
            # "sync_status": subscription.sync_status,
            # "last_synced": subscription.last_synced,
        }
