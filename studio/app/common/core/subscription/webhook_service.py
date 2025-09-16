import os
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import logger
from sqlmodel import Session

from studio.app.common.models.subscription import (
    CancellationReason,
    SubscriptionCancellation,
    SubscriptionUserAccount,
    SyncStatus,
    UserSubscription,
)


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
        # Additional logic can be added here if needed

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

    @staticmethod
    def get_webhook_secret() -> str:
        webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
        if not webhook_secret:
            raise ValueError("STRIPE_WEBHOOK_SECRET environment variable is not set")
        return webhook_secret
