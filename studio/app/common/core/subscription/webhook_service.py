from enum import Enum
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import stripe
from sqlmodel import Session

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.subscription.checkout_service import CheckoutService
from studio.app.common.core.subscription.subscription_service import SubscriptionService
from studio.app.common.models.subscription import (
    CancellationReason,
    SubscriptionCancellation,
    SubscriptionPlans,
    SubscriptionUserAccount,
    SubscriptionUserPurchase,
    SyncStatus,
    UserSubscription,
)

logger = AppLogger.get_logger()


class StripeWebhookEvent(Enum):
    CHECKOUT_SESSION_COMPLETED = "checkout.session.completed"
    INVOICE_PAYMENT_FAILED = "invoice.payment_failed"
    CUSTOMER_SUBSCRIPTION_DELETED = "customer.subscription.deleted"
    SUBSCRIPTION_SCHEDULE_RELEASED = "subscription_schedule.released"
    INVOICE_PAYMENT_SUCCEEDED = "invoice.payment_succeeded"


class WebhookService:
    stripe.api_key = SubscriptionService.get_stripe_key()

    """Service class for handling Stripe webhooks"""

    @staticmethod
    def handle_checkout_completed(
        db: Session, session_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle checkout.session.completed webhook

        Args:
            db: Database session
            session_data: Webhook session data from Stripe

        Returns:
            Dict with processing results

        Raises:
            ValueError: If validation fails
            Exception: For processing errors
        """
        try:
            session_id = session_data.get("id")
            logger.info(f"Webhook: Processing checkout session completed: {session_id}")

            # Extract data from webhook payload
            customer_id = session_data.get("customer")
            payment_status = session_data.get("payment_status")

            # Get metadata from the session (should contain user_id and plan_id)
            metadata = session_data.get("metadata", {})
            user_id = metadata.get("user_id")
            plan_id = metadata.get("plan_id")

            # Validate required data
            if not user_id or not plan_id:
                raise ValueError(
                    f"Missing user_id or plan_id in session metadata: {session_id}"
                )

            # Convert to integers
            try:
                user_id = int(user_id)
                plan_id = int(plan_id)
            except (ValueError, TypeError):
                raise ValueError(
                    f"Invalid user_id or plan_id format in session metadata: "
                    f"{session_id}"
                )

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
                    f"Webhook: Duplicate processing detected for user {user_id}, "
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
                    "webhook_processed": True,
                }

            # 2. Verify payment status from webhook data
            if payment_status != "paid":
                raise ValueError(f"Payment not completed. Status: {payment_status}")

            # 3. Get subscription plan
            plan = CheckoutService.get_subscription_plan(db, plan_id)
            if not plan:
                raise ValueError(f"Subscription plan not found: {plan_id}")

            # 4. Get or create Stripe provider
            stripe_provider_id = CheckoutService.get_or_create_stripe_provider(db)

            # 5. Create or update user account
            CheckoutService.create_or_update_user_account(
                db, user_id, stripe_provider_id, customer_id
            )

            # 6. SET DEFAULT PAYMENT METHOD
            CheckoutService.set_default_payment_method(session_id, customer_id)

            # 7. Calculate expiration date
            expiration_date = CheckoutService.calculate_expiration_date(
                plan.billing_cycle
            )

            # 8. Create or update subscription
            subscription_user_id = CheckoutService.create_or_update_subscription(
                db, user_id, plan_id, expiration_date
            )

            # 9. Record purchase (optionally store session_id for reference)
            purchase = CheckoutService.record_purchase(db, plan_id, user_id)

            # 10. Commit all changes
            db.commit()

            logger.info(
                f"Webhook: Successfully processed checkout for user {user_id}, "
                f"plan {plan_id}, session {session_id}"
            )

            return {
                "success": True,
                "subscription_user_id": subscription_user_id,
                "purchase_id": purchase.id,
                "expiration_date": expiration_date,
                "message": "Subscription activated successfully via webhook",
                "webhook_processed": True,
                "session_id": session_id,
            }

        except Exception as e:
            logger.error(
                f"Webhook: Error processing checkout success for session "
                f"{session_id}: {str(e)}"
            )
            db.rollback()
            raise

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
        logger.info(f"Webhook: Subscription cancelled for customer: {customer_id}")
        stripe_subscription_id = subscription_data.get("id")
        logger.info(f"Webhook: Subscription cancelled: {stripe_subscription_id}")

        # Find user account by customer ID
        user_account = (
            db.query(SubscriptionUserAccount)
            .filter(SubscriptionUserAccount.provider_customer_id == customer_id)
            .first()
        )

        logger.info(f"Webhook: Found user account: {user_account}")

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

                # Remove any scheduled downgrade
                subscription.scheduled_downgrade = False

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

    @staticmethod
    def handle_subscription_schedule_released(db: Session, data: dict):
        """
        Handle when a subscription schedule is released (plan change executed)
        Event: subscription_schedule.released
        """
        try:
            logger.info("Processing subscription_schedule.released webhook")

            # Get the subscription schedule data
            subscription_id = data.get("subscription")
            customer_id = data.get("customer")

            if not subscription_id:
                logger.error("No subscription ID in schedule released event")
                return

            # Get the subscription details from Stripe
            subscription = stripe.Subscription.retrieve(subscription_id)
            current_period_end = subscription["items"]["data"][0]["current_period_end"]

            # Get customer details to find user
            customer = stripe.Customer.retrieve(customer_id)
            customer_email = customer.get("email")

            if not customer_email:
                logger.error(f"No email found for customer {customer_id}")
                return

            # Find user by email
            from studio.app.common.models.user import User  # Adjust import as needed

            user = db.query(User).filter(User.email == customer_email).first()

            if not user:
                logger.error(f"No user found with email {customer_email}")
                return

            # Find the plan by matching the price or metadata
            new_plan_id = None

            # Try to get plan_id from subscription metadata first
            if "plan_id" in subscription.get("metadata", {}):
                new_plan_id = int(subscription["metadata"]["plan_id"])
            else:
                # Fallback: find plan by price and currency
                price = subscription["items"]["data"][0]["price"]
                plan = (
                    db.query(SubscriptionPlans)
                    .filter(
                        SubscriptionPlans.price == price["unit_amount"],
                        SubscriptionPlans.currency
                        == (1 if price["currency"] == "usd" else 2),
                    )
                    .first()
                )

                if plan:
                    new_plan_id = plan.id

            if not new_plan_id:
                logger.error(f"Could not determine new plan ID for user {user.id}")
                return

            # Update the user's subscription in database
            # Find the active subscription for this user
            user_subscription = (
                db.query(UserSubscription)
                .filter(
                    UserSubscription.user_id == user.id,
                    UserSubscription.expiration > datetime.now(timezone.utc),
                )
                .first()
            )

            if user_subscription:
                # Update the subscription with new plan details
                user_subscription.plan_id = new_plan_id
                user_subscription.updated_at = datetime.now(timezone.utc)
                user_subscription.expiration = datetime.fromtimestamp(
                    current_period_end
                )
            else:
                logger.error(f"No active subscription found for user {user.id}")
                return

            db.commit()

            logger.info(
                f"Successfully updated subscription for user {user.id} to plan "
                f"{new_plan_id} via webhook"
            )

        except Exception as e:
            logger.error(
                f"Error processing subscription_schedule.released webhook: {str(e)}"
            )
            db.rollback()
            raise

    @staticmethod
    def handle_subscription_payment_succeeded(
        db: Session, invoice_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle invoice.payment_succeeded webhook for subscription renewals

        Args:
            db: Database session
            invoice_data: Webhook invoice data from Stripe

        Returns:
            Dict with processing results

        Raises:
            ValueError: If validation fails
            Exception: For processing errors
        """
        try:
            invoice_id = invoice_data.get("id")
            logger.info(
                f"Webhook: Processing subscription payment succeeded: {invoice_id}"
            )

            # Extract data from webhook payload
            customer_id = invoice_data.get("customer")
            subscription_id = invoice_data.get("subscription")
            payment_status = invoice_data.get("status")
            amount_paid = invoice_data.get("amount_paid", 0)
            billing_reason = invoice_data.get("billing_reason")

            logger.info(
                f"Webhook: customer_id={customer_id}, subscription_id={subscription_id}"
            )
            logger.info(
                f"Webhook: payment_status={payment_status}, amount={amount_paid}, "
                f"billing_reason={billing_reason}"
            )

            # Only process subscription cycle payments (not initial payments)
            if billing_reason not in ["subscription_cycle", "subscription_update"]:
                logger.info(
                    f"Webhook: Skipping invoice - billing_reason: {billing_reason}"
                )
                return {
                    "success": True,
                    "message": f"Invoice skipped - billing_reason: {billing_reason}",
                    "webhook_processed": True,
                    "skipped": True,
                }

            # Validate required data
            if not customer_id or not subscription_id:
                raise ValueError(
                    f"Missing customer_id or subscription_id in invoice: {invoice_id}"
                )

            # Verify payment was successful
            if payment_status != "paid":
                raise ValueError(f"Payment not completed. Status: {payment_status}")

            # 1. Find user by Stripe customer ID
            try:
                logger.info(f"Webhook: Finding user by customer_id: {customer_id}")
                user_account = (
                    db.query(SubscriptionUserAccount)
                    .filter(SubscriptionUserAccount.provider_customer_id == customer_id)
                    .first()
                )

                if not user_account:
                    raise ValueError(f"User not found for customer_id: {customer_id}")

                user_id = user_account.user_id
                logger.info(f"Webhook: Found user_id: {user_id}")

            except Exception as e:
                logger.error(f"Webhook: Error finding user: {str(e)}")
                raise

            # 2. Find active subscription by Stripe subscription ID
            try:
                user_subscription = (
                    db.query(UserSubscription)
                    .filter(
                        UserSubscription.user_id == user_id,
                        UserSubscription.expiration > datetime.now(timezone.utc),
                    )
                    .order_by(UserSubscription.expiration.desc())
                    .first()
                )

                if not user_subscription:
                    raise ValueError(
                        f"Active subscription not found for user: {user_id}"
                    )

                plan_id = user_subscription.plan_id
                logger.info(f"Webhook: Found subscription plan_id: {plan_id}")

            except Exception as e:
                logger.error(f"Webhook: Error finding subscription: {str(e)}")
                raise

            # 3. Get subscription plan details
            try:
                logger.info(f"Webhook: Getting subscription plan: {plan_id}")
                plan = CheckoutService.get_subscription_plan(db, plan_id)
                if not plan:
                    raise ValueError(f"Subscription plan not found: {plan_id}")

            except Exception as e:
                logger.error(f"Webhook: Error getting subscription plan: {str(e)}")
                raise

            # 4. Calculate new expiration date (extend from current expiration)
            try:
                logger.info("Webhook: Calculating new expiration date...")

                current_expiration = user_subscription.expiration
                billing_cycle = plan.billing_cycle

                # Calculate extension period
                if billing_cycle == "1":
                    new_expiration = current_expiration + timedelta(days=30)
                elif billing_cycle == "2":
                    new_expiration = current_expiration + timedelta(days=365)
                else:
                    # Default to monthly if unknown
                    new_expiration = current_expiration + timedelta(days=30)
                    logger.warning(
                        f"Unknown billing cycle: {billing_cycle}, defaulting to monthly"
                    )

                logger.info(
                    f"Webhook: Extending expiration from {current_expiration} "
                    f"to {new_expiration}"
                )

            except Exception as e:
                logger.error(f"Webhook: Error calculating expiration: {str(e)}")
                raise

            # 5. Update subscription expiration
            try:
                logger.info("Webhook: Updating subscription expiration...")
                user_subscription.expiration = new_expiration
                user_subscription.updated_at = datetime.now(timezone.utc)

            except Exception as e:
                logger.error(f"Webhook: Error updating subscription: {str(e)}")
                raise

            # 6. Record the payment/purchase
            try:
                logger.info("Webhook: Recording subscription renewal purchase...")

                # Create purchase record for the renewal
                purchase = SubscriptionUserPurchase(
                    user_id=user_id,
                    plan_id=plan_id,
                    created_at=datetime.now(timezone.utc),
                )

                db.add(purchase)
                db.flush()  # Get the purchase ID

                logger.info(f"Webhook: Purchase recorded with ID: {purchase.id}")

            except Exception as e:
                logger.error(f"Webhook: Error recording purchase: {str(e)}")
                raise

            # 7. Commit all changes
            db.commit()

            logger.info(
                f"Webhook: Successfully processed subscription renewal for user "
                f"{user_id}, plan {plan_id}, invoice {invoice_id}. "
                f"New expiration: {new_expiration}"
            )

            return {
                "success": True,
                "user_id": user_id,
                "subscription_id": user_subscription.id,
                "purchase_id": purchase.id,
                "old_expiration": current_expiration.isoformat(),
                "new_expiration": new_expiration.isoformat(),
                "amount_paid": amount_paid / 100,
                "message": "Subscription renewed successfully via webhook",
                "webhook_processed": True,
                "invoice_id": invoice_id,
            }

        except Exception as e:
            logger.error(
                f"Webhook: Error processing subscription payment for invoice "
                f"{invoice_id}: {str(e)}"
            )
            db.rollback()
            raise

    @staticmethod
    def get_webhook_secret() -> str:
        webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
        if not webhook_secret:
            raise ValueError("STRIPE_WEBHOOK_SECRET environment variable is not set")
        return webhook_secret

    @staticmethod
    def dispatch_webhook_event(
        db: Session, event_type: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Dispatch webhook events to appropriate handlers

        Args:
            db: Database session
            event_type: The type of webhook event
            data: Webhook event data

        Returns:
            Dict with processing results
        """
        try:
            match event_type:
                case StripeWebhookEvent.CHECKOUT_SESSION_COMPLETED.value:
                    logger.info("Handling checkout.session.completed")
                    return WebhookService.handle_checkout_completed(db, data)

                case StripeWebhookEvent.INVOICE_PAYMENT_FAILED.value:
                    logger.info("Handling invoice.payment_failed")
                    WebhookService.handle_payment_failed(db, data)
                    return {
                        "success": True,
                        "message": "Payment failed event processed",
                    }

                case StripeWebhookEvent.CUSTOMER_SUBSCRIPTION_DELETED.value:
                    logger.info("Handling customer.subscription.deleted")
                    WebhookService.handle_subscription_cancelled(db, data)
                    return {
                        "success": True,
                        "message": "Subscription cancellation processed",
                    }

                case StripeWebhookEvent.SUBSCRIPTION_SCHEDULE_RELEASED.value:
                    logger.info("Handling subscription_schedule.released")
                    WebhookService.handle_subscription_schedule_released(db, data)
                    return {
                        "success": True,
                        "message": "Subscription schedule release processed",
                    }

                case StripeWebhookEvent.INVOICE_PAYMENT_SUCCEEDED.value:
                    logger.info("Handling invoice.payment_succeeded")
                    return WebhookService.handle_subscription_payment_succeeded(
                        db, data
                    )

                case _:
                    logger.info(f"Unhandled webhook event type: {event_type}")
                    return {
                        "success": True,
                        "message": f"Unhandled event type: {event_type}",
                    }

        except Exception as e:
            logger.error(f"Error dispatching webhook event {event_type}: {str(e)}")
            raise
