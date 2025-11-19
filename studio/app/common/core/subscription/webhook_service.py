import os
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Dict

import stripe
from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from sqlmodel import Session

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.subscription.checkout_service import CheckoutService
from studio.app.common.core.subscription.subscription_service import (
    SubscriptionCurrencyType,
    SubscriptionService,
)
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


class StripeWebhookEvent(StrEnum):
    CHECKOUT_SESSION_COMPLETED = "checkout.session.completed"
    INVOICE_PAYMENT_FAILED = "invoice.payment_failed"
    CUSTOMER_SUBSCRIPTION_DELETED = "customer.subscription.deleted"
    SUBSCRIPTION_SCHEDULE_RELEASED = "subscription_schedule.released"
    INVOICE_PAYMENT_SUCCEEDED = "invoice.payment_succeeded"


class BILLING_CYCLE(StrEnum):
    MONTHLY = "1"
    YEARLY = "2"


class PaymentStatus(StrEnum):
    PAID = "paid"


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
            HTTPException: If validation fails or processing errors occur
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
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Missing user_id or plan_id in session metadata: "
                        f"{session_id}"
                    ),
                )

            # Convert to integers
            try:
                user_id = int(user_id)
                plan_id = int(plan_id)
            except (ValueError, TypeError):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Invalid user_id or plan_id format in session metadata: "
                        f"{session_id}"
                    ),
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
                    > SubscriptionService.get_current_datetime()
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
                        UserSubscription.expiration
                        > SubscriptionService.get_current_datetime(),
                    )
                    .first()
                )

                # Only treat as duplicate if there's an ACTIVE subscription
                # If subscription is expired/cancelled, allow new purchase to proceed
                if existing_subscription:
                    logger.info(
                        f"Webhook: Duplicate processing detected for user {user_id}, "
                        f"session {session_id}"
                    )
                    return {
                        "success": True,
                        "subscription_user_id": existing_subscription.id,
                        "purchase_id": existing_purchase.id,
                        "expiration_date": existing_subscription.expiration,
                        "message": "Subscription already processed successfully",
                        "webhook_processed": True,
                    }
                else:
                    logger.info(
                        f"Webhook: Found recent purchase but no active subscription "
                        f"for user {user_id}. Proceeding with new subscription."
                    )

            # 2. Verify payment status from webhook data
            if payment_status != PaymentStatus.PAID:
                raise HTTPException(
                    status_code=400,
                    detail=f"Payment not completed. Status: {payment_status}",
                )

            # 3. Get subscription plan
            plan = CheckoutService.get_subscription_plan(db, plan_id)
            if not plan:
                raise HTTPException(
                    status_code=404, detail=f"Subscription plan not found: {plan_id}"
                )

            # 4. Get or create Stripe provider
            stripe_provider_id = CheckoutService.get_or_create_stripe_provider(db)

            # 5. Create or update user account
            CheckoutService.create_or_update_user_account(
                db, user_id, stripe_provider_id, customer_id
            )

            # 6. SET DEFAULT PAYMENT METHOD
            CheckoutService.set_default_payment_method(session_id, customer_id)

            # 7. Get expiration date from Stripe subscription
            stripe_subscription_id = session_data.get("subscription")
            if not stripe_subscription_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"No subscription ID found in session: {session_id}",
                )

            # Retrieve subscription from Stripe to get the current_period_end
            try:
                stripe_subscription = stripe.Subscription.retrieve(
                    stripe_subscription_id
                )

                # Log the full subscription object for debugging
                logger.info(
                    f"Webhook: Retrieved subscription {stripe_subscription_id}, "
                    f"status: {getattr(stripe_subscription, 'status', 'unknown')}"
                )

                # Access current_period_end - use getattr to handle both objects
                # and dicts
                current_period_end = getattr(
                    stripe_subscription, "current_period_end", None
                )
                if current_period_end is None and isinstance(stripe_subscription, dict):
                    current_period_end = stripe_subscription.get("current_period_end")

                # Check for trial end as well (in case of trial subscriptions)
                trial_end = getattr(stripe_subscription, "trial_end", None)
                if trial_end is None and isinstance(stripe_subscription, dict):
                    trial_end = stripe_subscription.get("trial_end")

                # Also check current_period_start for fallback calculation
                current_period_start = getattr(
                    stripe_subscription, "current_period_start", None
                )
                if current_period_start is None and isinstance(
                    stripe_subscription, dict
                ):
                    current_period_start = stripe_subscription.get(
                        "current_period_start"
                    )

                # Debug logging to see raw values
                logger.info(
                    f"Webhook: Raw subscription data - "
                    f"current_period_end: {current_period_end}, "
                    f"trial_end: {trial_end}, "
                    f"current_period_start: {current_period_start}, "
                    f"created: {getattr(stripe_subscription, 'created', None)}"
                )

                logger.info(
                    f"Webhook: Stripe subscription type: {type(stripe_subscription)}, "
                    f"current_period_end: {current_period_end}, trial_end: {trial_end}, "
                    f"current_period_start: {current_period_start}"
                )

                # For trial subscriptions, use trial_end.
                # Otherwise use current_period_end
                expiration_timestamp = None
                if trial_end:
                    # Subscription is in trial period
                    expiration_timestamp = trial_end
                    logger.info(
                        f"Webhook: Subscription is in trial period, "
                        f"using trial_end: {trial_end}"
                    )
                elif current_period_end:
                    # Regular subscription
                    expiration_timestamp = current_period_end
                    logger.info(
                        f"Webhook: Regular subscription, "
                        f"using current_period_end: {current_period_end}"
                    )
                elif current_period_start:
                    # Fallback: calculate expiration as 1 month from start
                    # This handles edge case where subscription is just created
                    start_date = datetime.fromtimestamp(current_period_start)
                    expiration_date = start_date + relativedelta(months=1)
                    expiration_timestamp = int(expiration_date.timestamp())
                    logger.warning(
                        f"Webhook: current_period_end not available, "
                        f"calculated from current_period_start: {expiration_date}"
                    )
                else:
                    # Final fallback: Try to get expiration from the latest invoice
                    logger.warning(
                        "Webhook: No period data found in subscription, "
                        "trying to get from latest invoice"
                    )
                    try:
                        latest_invoice_id = getattr(
                            stripe_subscription, "latest_invoice", None
                        )
                        if latest_invoice_id:
                            invoice = stripe.Invoice.retrieve(latest_invoice_id)
                            lines = invoice.get("lines", {}).get("data", [])
                            if lines:
                                period_end = lines[0].get("period", {}).get("end")
                                if period_end:
                                    expiration_timestamp = period_end
                                    logger.info(
                                        f"Webhook: Got expiration from invoice: "
                                        f"{datetime.fromtimestamp(period_end)}"
                                    )
                                else:
                                    raise HTTPException(
                                        status_code=400,
                                        detail=(
                                            f"No period end found in invoice lines for "
                                            f"subscription: {stripe_subscription_id}"
                                        ),
                                    )
                            else:
                                raise HTTPException(
                                    status_code=400,
                                    detail=(
                                        f"No invoice lines found for subscription: "
                                        f"{stripe_subscription_id}"
                                    ),
                                )
                        else:
                            raise HTTPException(
                                status_code=400,
                                detail=(
                                    f"No expiration date found in subscription: "
                                    f"{stripe_subscription_id}"
                                ),
                            )
                    except stripe.error.StripeError as e:
                        logger.error(f"Webhook: Error retrieving invoice: {str(e)}")
                        raise HTTPException(
                            status_code=500,
                            detail=f"Error retrieving invoice: {str(e)}",
                        )

                # Convert Unix timestamp to datetime
                expiration_date = datetime.fromtimestamp(expiration_timestamp)
                logger.info(
                    f"Webhook: Using expiration date from Stripe: {expiration_date} "
                    f"(Unix timestamp: {expiration_timestamp})"
                )

            except stripe.error.StripeError as e:
                logger.error(
                    f"Webhook: Stripe API error retrieving subscription: {str(e)}"
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Error retrieving subscription from Stripe: {str(e)}",
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

        except HTTPException as e:
            logger.error(
                f"Webhook: HTTPException processing checkout for session "
                f"{session_id}: {e.detail}"
            )
            db.rollback()
            raise  # Re-raise the original HTTPException with its details
        except Exception as e:
            logger.error(
                f"Webhook: Error processing checkout success for session "
                f"{session_id}: {str(e)}"
            )
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Error processing checkout success for session "
                    f"{session_id}: {str(e)}"
                ),
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
                    UserSubscription.expiration
                    > SubscriptionService.get_current_datetime(),
                )
                .first()
            )

            if subscription:
                subscription.sync_status = SyncStatus.FAILED
                subscription.updated_at = SubscriptionService.get_current_datetime()
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
                    UserSubscription.expiration
                    > SubscriptionService.get_current_datetime(),
                )
                .first()
            )

            if subscription:
                # Expire subscription immediately
                subscription.expiration = SubscriptionService.get_current_datetime()
                subscription.updated_at = SubscriptionService.get_current_datetime()

                # Remove any scheduled downgrade
                subscription.scheduled_downgrade = False

                # Find the most recent purchase for this user and plan
                purchase = (
                    db.query(SubscriptionUserPurchase)
                    .filter(
                        SubscriptionUserPurchase.user_id == user_account.user_id,
                        SubscriptionUserPurchase.plan_id == subscription.plan_id,
                    )
                    .order_by(SubscriptionUserPurchase.created_at.desc())
                    .first()
                )

                # Only record cancellation if we have a purchase record
                if purchase:
                    # Record cancellation
                    cancellation = SubscriptionCancellation(
                        cancelled_by_user_id=user_account.user_id,
                        purchases_id=purchase.id,
                        reason=CancellationReason.USER_REQUEST,
                        notes=(
                            f"Cancelled via Stripe webhook for subscription "
                            f"{stripe_subscription_id}"
                        ),
                    )
                    db.add(cancellation)
                    logger.info(
                        f"Recorded cancellation for user {user_account.user_id}, "
                        f"purchase {purchase.id}"
                    )
                else:
                    logger.warning(
                        f"No purchase record found for user {user_account.user_id}, "
                        f"plan {subscription.plan_id}. Skipping cancellation record."
                    )

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
                raise HTTPException(
                    status_code=400,
                    detail="No subscription ID in schedule released event",
                )

            # Get the subscription details from Stripe
            subscription = stripe.Subscription.retrieve(subscription_id)
            current_period_end = subscription["items"]["data"][0]["current_period_end"]

            # Get customer details to find user
            customer = stripe.Customer.retrieve(customer_id)
            customer_email = customer.get("email")

            if not customer_email:
                raise HTTPException(
                    status_code=400, detail=f"No email found for customer {customer_id}"
                )

            # Find user by email
            from studio.app.common.models.user import User  # Adjust import as needed

            user = db.query(User).filter(User.email == customer_email).first()

            if not user:
                raise HTTPException(
                    status_code=404, detail=f"No user found with email {customer_email}"
                )

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
                        == SubscriptionCurrencyType.get_currency_enum(
                            price["currency"]
                        ),
                    )
                    .first()
                )

                if plan:
                    new_plan_id = plan.id

            if not new_plan_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not determine new plan ID for user {user.id}",
                )

            # Update the user's subscription in database
            # Find the active subscription for this user
            user_subscription = (
                db.query(UserSubscription)
                .filter(
                    UserSubscription.user_id == user.id,
                    UserSubscription.expiration
                    > SubscriptionService.get_current_datetime(),
                )
                .first()
            )

            if user_subscription:
                # Update the subscription with new plan details
                user_subscription.plan_id = new_plan_id
                user_subscription.updated_at = (
                    SubscriptionService.get_current_datetime()
                )
                user_subscription.expiration = datetime.fromtimestamp(
                    current_period_end
                )
            else:
                raise HTTPException(
                    status_code=404,
                    detail=f"No active subscription found for user {user.id}",
                )

            db.commit()

            logger.info(
                f"Successfully updated subscription for user {user.id} to plan "
                f"{new_plan_id} via webhook"
            )

        except HTTPException:
            db.rollback()
            raise HTTPException(status_code=400, detail="Invalid webhook data")
        except Exception as e:
            logger.error(
                f"Error processing subscription_schedule.released webhook: {str(e)}"
            )
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Error processing subscription_schedule.released webhook: "
                    f"{str(e)}"
                ),
            )

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
            HTTPException: If validation fails or processing errors occur
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
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Missing customer_id or subscription_id in invoice: "
                        f"{invoice_id}"
                    ),
                )

            # Verify payment was successful
            if payment_status != PaymentStatus.PAID:
                raise HTTPException(
                    status_code=400,
                    detail=f"Payment not completed. Status: {payment_status}",
                )

            # 1. Find user by Stripe customer ID
            try:
                logger.info(f"Webhook: Finding user by customer_id: {customer_id}")

                user_account = (
                    db.query(SubscriptionUserAccount)
                    .filter(SubscriptionUserAccount.provider_customer_id == customer_id)
                    .first()
                )

                logger.info(f"Webhook: Query result: {user_account}")

                if not user_account:
                    raise HTTPException(
                        status_code=404,
                        detail=f"User not found for customer_id: {customer_id}",
                    )

                user_id = user_account.user_id
                logger.info(f"Webhook: Found user_id: {user_id}")

            except HTTPException as http_exc:
                logger.error(f"Webhook: HTTPException finding user: {http_exc.detail}")
                raise  # Re-raise the original exception
            except Exception as e:
                logger.error(f"Webhook: Error finding user: {str(e)}")
                raise HTTPException(
                    status_code=500, detail=f"Error finding user: {str(e)}"
                )

            # 2. Find active subscription by Stripe subscription ID
            try:
                logger.info(
                    f"Webhook: Finding active subscription for user_id: {user_id}"
                )
                user_subscription = (
                    db.query(UserSubscription)
                    .filter(
                        UserSubscription.user_id == user_id,
                        UserSubscription.expiration
                        > SubscriptionService.get_current_datetime(),
                    )
                    .order_by(UserSubscription.expiration.desc())
                    .first()
                )

                if not user_subscription:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Active subscription not found for user: {user_id}",
                    )

                plan_id = user_subscription.plan_id
                logger.info(f"Webhook: Found subscription plan_id: {plan_id}")

            except HTTPException:
                raise HTTPException(status_code=400, detail="Invalid webhook data")
            except Exception as e:
                logger.error(f"Webhook: Error finding subscription: {str(e)}")
                raise HTTPException(
                    status_code=500, detail=f"Error finding subscription: {str(e)}"
                )

            # 3. Get subscription plan details
            try:
                logger.info(f"Webhook: Getting subscription plan: {plan_id}")
                plan = CheckoutService.get_subscription_plan(db, plan_id)
                if not plan:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Subscription plan not found: {plan_id}",
                    )

            except HTTPException:
                raise HTTPException(status_code=400, detail="Invalid webhook data")
            except Exception as e:
                logger.error(f"Webhook: Error getting subscription plan: {str(e)}")
                raise HTTPException(
                    status_code=500, detail=f"Error getting subscription plan: {str(e)}"
                )

            # 4. Get expiration date from invoice line items
            try:
                logger.info("Webhook: Getting expiration date from invoice...")

                current_expiration = user_subscription.expiration

                # Get the period end from invoice line items
                lines = invoice_data.get("lines", {}).get("data", [])
                if not lines:
                    raise HTTPException(
                        status_code=400,
                        detail=f"No line items found in invoice: {invoice_id}",
                    )

                # Get the period end from the first line item
                period_end_timestamp = lines[0].get("period", {}).get("end")
                if not period_end_timestamp:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"No period end found in invoice line items: "
                            f"{invoice_id}"
                        ),
                    )

                # Convert Unix timestamp to datetime
                new_expiration = datetime.fromtimestamp(period_end_timestamp)

                logger.info(
                    f"Webhook: Using expiration date from invoice: {new_expiration} "
                    f"(Unix timestamp: {period_end_timestamp})"
                )
                logger.info(
                    f"Webhook: Extending expiration from {current_expiration} "
                    f"to {new_expiration}"
                )

            except HTTPException:
                raise
            except Exception as e:
                logger.error(
                    f"Webhook: Error getting expiration from invoice: {str(e)}"
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Error getting expiration from invoice: {str(e)}",
                )

            # 5. Update subscription expiration
            try:
                logger.info("Webhook: Updating subscription expiration...")
                user_subscription.expiration = new_expiration
                user_subscription.updated_at = (
                    SubscriptionService.get_current_datetime()
                )

            except Exception as e:
                logger.error(f"Webhook: Error updating subscription: {str(e)}")
                raise HTTPException(
                    status_code=500, detail=f"Error updating subscription: {str(e)}"
                )

            # 6. Record the payment/purchase
            try:
                logger.info("Webhook: Recording subscription renewal purchase...")

                # Create purchase record for the renewal
                purchase = SubscriptionUserPurchase(
                    user_id=user_id,
                    plan_id=plan_id,
                    created_at=SubscriptionService.get_current_datetime(),
                )

                db.add(purchase)
                db.flush()  # Get the purchase ID

                logger.info(f"Webhook: Purchase recorded with ID: {purchase.id}")

            except Exception as e:
                logger.error(f"Webhook: Error recording purchase: {str(e)}")
                raise HTTPException(
                    status_code=500, detail=f"Error recording purchase: {str(e)}"
                )

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

        except HTTPException:
            db.rollback()
            raise HTTPException(status_code=400, detail="Invalid webhook data")
        except Exception as e:
            logger.error(
                f"Webhook: Error processing subscription payment for invoice "
                f"{invoice_id}: {str(e)}"
            )
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Error processing subscription payment for invoice "
                    f"{invoice_id}: {str(e)}"
                ),
            )

    @staticmethod
    def get_webhook_secret() -> str:
        webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
        if not webhook_secret:
            raise HTTPException(
                status_code=500,
                detail="STRIPE_WEBHOOK_SECRET environment variable is not set",
            )
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
                case StripeWebhookEvent.CHECKOUT_SESSION_COMPLETED:
                    logger.info("Handling checkout.session.completed")
                    return WebhookService.handle_checkout_completed(db, data)

                case StripeWebhookEvent.INVOICE_PAYMENT_FAILED:
                    logger.info("Handling invoice.payment_failed")
                    WebhookService.handle_payment_failed(db, data)
                    return {
                        "success": True,
                        "message": "Payment failed event processed",
                    }

                case StripeWebhookEvent.CUSTOMER_SUBSCRIPTION_DELETED:
                    logger.info("Handling customer.subscription.deleted")
                    WebhookService.handle_subscription_cancelled(db, data)
                    return {
                        "success": True,
                        "message": "Subscription cancellation processed",
                    }

                case StripeWebhookEvent.SUBSCRIPTION_SCHEDULE_RELEASED:
                    logger.info("Handling subscription_schedule.released")
                    WebhookService.handle_subscription_schedule_released(db, data)
                    return {
                        "success": True,
                        "message": "Subscription schedule release processed",
                    }

                case StripeWebhookEvent.INVOICE_PAYMENT_SUCCEEDED:
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

        except HTTPException:
            raise HTTPException(status_code=400, detail="Invalid webhook data")
        except Exception as e:
            logger.error(f"Error dispatching webhook event {event_type}: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Error dispatching webhook event {event_type}: {str(e)}",
            )
