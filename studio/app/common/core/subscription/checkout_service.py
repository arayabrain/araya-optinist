from datetime import datetime
from typing import Any, Dict, Optional

import stripe
from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from sqlmodel import Enum, Session

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.subscription.subscription_service import SubscriptionService
from studio.app.common.models.subscription import (
    SubscriptionPlans,
    SubscriptionProvider,
    SubscriptionUserAccount,
    SubscriptionUserPurchase,
    UserSubscription,
)
from studio.app.common.models.user import User
from studio.app.common.schemas.subscriptions import (
    CreateCheckoutSessionRequest,
    CreateCheckoutSessionResponse,
)

logger = AppLogger.get_logger()
STRIPE_CALLBACK_URL = SubscriptionService.get_base_url()


class SUBSCRIPTION_ACTIVE_STATUS(Enum):
    ACTIVE = "1"
    INACTIVE = "0"


class SUBSCRIPTION_SYNC_STATUS(Enum):
    SYNCED = "synced"
    PENDING = "pending"
    FAILED = "failed"


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
                UserSubscription.expiration
                > SubscriptionService.get_current_datetime(),
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
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Error processing subscription_schedule.released webhook: "
                    f"{str(e)}"
                ),
            )

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
                SubscriptionPlans.status == SUBSCRIPTION_ACTIVE_STATUS.ACTIVE,
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
        return SubscriptionService.get_current_datetime() + relativedelta(
            months=billing_cycle
        )

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
            user_account.updated_at = SubscriptionService.get_current_datetime()

        return user_account

    @staticmethod
    def set_default_payment_method(session_id: str, customer_id: str):
        try:
            # Retrieve the full session from Stripe
            checkout_session = stripe.checkout.Session.retrieve(session_id)

            payment_method_id = None

            # For subscription mode checkouts, get payment method from subscription
            if checkout_session.get("mode") == "subscription" and checkout_session.get(
                "subscription"
            ):
                subscription_id = checkout_session["subscription"]

                # Retrieve the subscription to get the payment method
                subscription = stripe.Subscription.retrieve(subscription_id)

                if subscription.default_payment_method:
                    payment_method_id = subscription.default_payment_method
                elif len(subscription.items.data) > 0:
                    # Fallback: get from subscription items if available
                    payment_method_id = getattr(
                        subscription.items.data[0], "default_payment_method", None
                    )

            # For payment mode checkouts, get from payment_intent
            elif checkout_session.get("payment_intent"):
                payment_intent = stripe.PaymentIntent.retrieve(
                    checkout_session["payment_intent"], expand=["payment_method"]
                )
                if payment_intent.payment_method:
                    payment_method_id = payment_intent.payment_method.id

            # Alternative approach: Get the most recent payment method for the customer
            if not payment_method_id:
                # Get payment methods attached to this customer
                payment_methods = stripe.PaymentMethod.list(
                    customer=customer_id, type="card", limit=1
                )

                if payment_methods.data:
                    payment_method_id = payment_methods.data[0].id
                    logger.info(
                        f"Using most recent payment method {payment_method_id} "
                        f"for customer {customer_id}"
                    )

            if payment_method_id:
                try:
                    stripe.PaymentMethod.attach(
                        payment_method_id,
                        customer=customer_id,
                    )
                except stripe.error.InvalidRequestError as e:
                    if "already been attached" in str(e):
                        logger.info(
                            f"Payment method {payment_method_id} already attached "
                            f"to customer {customer_id}"
                        )
                    else:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Failed to attach payment method: {str(e)}",
                        )

                # Update customer's default payment method
                stripe.Customer.modify(
                    customer_id,
                    invoice_settings={"default_payment_method": payment_method_id},
                )

                logger.info(
                    f"Webhook: Set payment method {payment_method_id} as default "
                    f"for customer {customer_id}"
                )
            else:
                logger.warning(
                    f"Webhook: Could not find any payment method for session "
                    f"{session_id} and customer {customer_id}"
                )

        except stripe.error.StripeError as e:
            logger.error(
                f"Webhook: Failed to set default payment method for customer "
                f"{customer_id}: {str(e)}"
            )
            # Don't fail the entire webhook for this - continue processing
        except Exception as e:
            logger.error(
                f"Webhook: Unexpected error setting default payment method: {str(e)}"
            )

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
        # Check for existing subscription
        existing_subscription = (
            db.query(UserSubscription)
            .filter(
                UserSubscription.user_id == user_id,
            )
            .first()
        )

        if existing_subscription:
            # Update existing subscription
            existing_subscription.plan_id = plan_id
            existing_subscription.sync_status = SUBSCRIPTION_SYNC_STATUS.SYNCED
            existing_subscription.expiration = expiration_date
            existing_subscription.scheduled_downgrade = False
            existing_subscription.updated_at = (
                SubscriptionService.get_current_datetime()
            )
            return existing_subscription.id
        else:
            # Create new subscription
            new_subscription = UserSubscription(
                plan_id=plan_id,
                user_id=user_id,
                expiration=expiration_date,
                sync_status=SUBSCRIPTION_SYNC_STATUS.SYNCED,
            )
            db.add(new_subscription)
            db.flush()  # Get ID without committing
            return new_subscription.id

    @staticmethod
    def get_subscription_account(
        db: Session, user_id: int
    ) -> Optional[SubscriptionUserAccount]:
        """
        Retrieve subscription user account by user ID

        Args:
            db: Database session
            user_id: Internal user ID
            provider_id: Payment provider ID

        Returns:
            SubscriptionUserAccount object or None if not found
        """
        return (
            db.query(SubscriptionUserAccount)
            .filter(
                SubscriptionUserAccount.user_id == user_id,
            )
            .first()
        )

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
    async def handle_checkout_session(
        db: Session, request: CreateCheckoutSessionRequest, user: User
    ) -> CreateCheckoutSessionResponse:
        try:
            # Get subscription plan from database using plan_id as string
            plan = SubscriptionService.get_plan_by_id(db, int(request.plan_id))

            if not plan:
                raise HTTPException(
                    status_code=404, detail="Subscription plan not found"
                )

            # Validate that the plan has Stripe product and price IDs
            if not plan.stripe_price_id:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Subscription plan '{plan.name}' is not configured "
                        f"with a Stripe price ID"
                    ),
                )

            logger.info(
                f"Request details - plan: {plan.name}, "
                f"stripe_product_id: {plan.stripe_product_id}, "
                f"stripe_price_id: {plan.stripe_price_id}"
            )

            # Create Stripe checkout session using the price ID from the database
            try:
                logger.info("Initializing Stripe")
                subscription_account = CheckoutService.get_subscription_account(
                    db, user.id
                )
                customer_id = (
                    subscription_account.provider_customer_id
                    if subscription_account
                    else stripe.Customer.create(
                        email=user.email,
                        name=getattr(user, "name", ""),
                        metadata={"user_id": str(user.id)},
                    ).id
                )

                checkout_session = stripe.checkout.Session.create(
                    payment_method_types=["card", "link"],
                    line_items=[
                        {
                            "price": plan.stripe_price_id,
                            "quantity": 1,
                        }
                    ],
                    mode="subscription",
                    success_url=(
                        f"{STRIPE_CALLBACK_URL}/console/subscription/thanks"
                        "?session_id={CHECKOUT_SESSION_ID}"
                    ),
                    cancel_url=(f"{STRIPE_CALLBACK_URL}/console/subscription"),
                    customer=customer_id,
                    client_reference_id=str(user.id),
                    metadata={
                        "user_id": str(user.id),
                        "plan_id": request.plan_id,
                        "plan_name": plan.name,
                    },
                )

                return CreateCheckoutSessionResponse(
                    checkout_url=checkout_session.url, session_id=checkout_session.id
                )

            except stripe.error.StripeError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to create checkout session: {str(e)}",
                )

        except HTTPException:
            raise HTTPException(
                status_code=500,
                detail=("Error processing handle chekckout session request"),
            )
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Internal server error: {str(e)}"
            )
