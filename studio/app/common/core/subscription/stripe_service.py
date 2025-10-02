from enum import Enum
from typing import Optional

import stripe
from fastapi import HTTPException, status

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.subscription.checkout_service import CheckoutService
from studio.app.common.core.subscription.subscription_service import (
    SubscriptionCurrencyType,
    SubscriptionService,
)
from studio.app.common.schemas.subscriptions import (
    CreateCheckoutSessionResponse,
    CreateSetupIntentResponse,
    PaymentMethodResponse,
    UpdatePaymentMethodResponse,
)

logger = AppLogger.get_logger()
STRIPE_CALLBACK_URL = SubscriptionService.get_base_url()


class StripeSubscriptionStatus(Enum):
    """Stripe subscription status values"""

    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    UNPAID = "unpaid"
    PAUSED = "paused"


async def get_stripe_customer_by_email(email: str) -> Optional[stripe.Customer]:
    """Get Stripe customer by email"""
    try:
        stripe_customers = stripe.Customer.list(email=email, limit=1)
        return stripe_customers.data[0] if stripe_customers.data else None
    except stripe.error.StripeError as e:
        logger.error(f"Error fetching Stripe customer: {str(e)}")
        return None


class StripeService:

    @staticmethod
    async def get_default_payment_method(
        current_user,
    ) -> Optional[PaymentMethodResponse]:
        """
        Get user's default payment method
        """
        try:
            # Get user email to find Stripe customer
            user = current_user
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            logger.info(
                f"Fetching default pymt method for user {user.id} "
                f"with email {user.email}"
            )

            # Find Stripe customer by email
            stripe_customer = await get_stripe_customer_by_email(user.email)

            if not stripe_customer:
                logger.info(f"No Stripe customer found for user {user.id}")
                return None

            # Get default payment method
            default_pm_id = stripe_customer.invoice_settings.default_payment_method
            if not default_pm_id:
                logger.info(f"No default payment method set for user {user.id}")
                return None

            # Retrieve the payment method details
            payment_method = stripe.PaymentMethod.retrieve(default_pm_id)

            if payment_method.type != "card":
                logger.info(f"Default payment method is not a card for user {user.id}")
                return None

            card = payment_method.card
            return PaymentMethodResponse(
                id=payment_method.id,
                last4=card.last4,
                brand=card.brand,
                exp_month=card.exp_month,
                exp_year=card.exp_year,
                is_default=True,
            )

        except stripe.error.StripeError as e:
            logger.error(
                f"Stripe error when fetching default payment method for user "
                f"{user.id}: {str(e)}"
            )
            raise HTTPException(
                status_code=400,
                detail=f"Failed to fetch default payment method from Stripe: {str(e)}",
            )
        except Exception as e:
            logger.error(
                f"Error fetching default payment method for user {user.id}: {str(e)}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch default payment method: {str(e)}",
            )

    @staticmethod
    async def create_setup_intent(current_user):
        try:
            user = current_user

            # Get or create Stripe customer
            customer = await get_stripe_customer_by_email(user.email)

            if not customer:
                # Create new Stripe customer
                customer = stripe.Customer.create(
                    email=user.email,
                    name=getattr(user, "name", ""),
                    metadata={"user_id": str(user.id)},
                )
                logger.info(f"Created new Stripe customer for user {user.id}")

            # Create SetupIntent
            setup_intent = stripe.SetupIntent.create(
                customer=customer.id,
                payment_method_types=["card"],
                usage="off_session",  # For future payments
            )

            return CreateSetupIntentResponse(
                success=True,
                client_secret=setup_intent.client_secret,
                setup_intent_id=setup_intent.id,
                message="Setup intent created successfully",
            )

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating setup intent: {str(e)}")
            raise HTTPException(
                status_code=400, detail=f"Payment processing error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Error creating setup intent: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to create setup intent")

    @staticmethod
    async def update_default_payment_method(current_user, payment_method_id: str):
        """
        Update the default payment method for a user's subscription
        """
        try:
            user = current_user

            # Get Stripe customer
            customer = await get_stripe_customer_by_email(user.email)
            if not customer:
                raise HTTPException(
                    status_code=404, detail="No Stripe customer found for user"
                )

            # Verify the payment method exists and belongs to this customer
            try:
                payment_method = stripe.PaymentMethod.retrieve(payment_method_id)
            except stripe.error.InvalidRequestError:
                raise HTTPException(status_code=404, detail="Payment method not found")

            # Attach payment method to customer if not already attached
            if payment_method.customer != customer.id:
                stripe.PaymentMethod.attach(
                    payment_method_id,
                    customer=customer.id,
                )

            # Set as default payment method for customer
            stripe.Customer.modify(
                customer.id,
                invoice_settings={"default_payment_method": payment_method_id},
            )

            # Update default payment method for active subscriptions
            subscriptions = stripe.Subscription.list(
                customer=customer.id, status=StripeSubscriptionStatus.ACTIVE
            )

            updated_subscriptions = 0
            for subscription in subscriptions.data:
                stripe.Subscription.modify(
                    subscription.id, default_payment_method=payment_method_id
                )
                updated_subscriptions += 1

            logger.info(
                f"Updated payment method for user {user.id}, "
                f"updated {updated_subscriptions} subscriptions"
            )

            return UpdatePaymentMethodResponse(
                success=True,
                message=(
                    f"Payment method updated successfully. "
                    f"Updated {updated_subscriptions} active subscriptions."
                ),
                payment_method_id=payment_method_id,
            )

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error updating payment method: {str(e)}")
            raise HTTPException(
                status_code=400, detail=f"Payment processing error: {str(e)}"
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating payment method: {str(e)}")
            raise HTTPException(
                status_code=500, detail="Failed to update payment method"
            )

    @staticmethod
    async def delete_payment_method(current_user, payment_method_id: str):
        """
        Delete a payment method (cannot delete if it's default for active subscriptions)
        """
        try:
            user = current_user

            # Get Stripe customer
            customer = await get_stripe_customer_by_email(user.email)
            if not customer:
                raise HTTPException(
                    status_code=404, detail="No Stripe customer found for user"
                )

            # Verify the payment method exists and belongs to this customer
            try:
                payment_method = stripe.PaymentMethod.retrieve(payment_method_id)
                if payment_method.customer != customer.id:
                    raise HTTPException(
                        status_code=403,
                        detail="Payment method does not belong to this user",
                    )
            except stripe.error.InvalidRequestError:
                raise HTTPException(status_code=404, detail="Payment method not found")

            # Check if this is the default payment method for customer
            if customer.invoice_settings.default_payment_method == payment_method_id:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Cannot delete the default payment method. "
                        "Please set a new default first."
                    ),
                )

            # Check if this is the default payment method for any active subscriptions
            subscriptions = stripe.Subscription.list(
                customer=customer.id, status=StripeSubscriptionStatus.ACTIVE
            )

            for subscription in subscriptions.data:
                if subscription.default_payment_method == payment_method_id:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Cannot delete payment method that is default for "
                            "active subscription"
                        ),
                    )

            # Detach the payment method
            stripe.PaymentMethod.detach(payment_method_id)

            return {"success": True, "message": "Payment method deleted successfully"}

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error deleting payment method: {str(e)}")
            raise HTTPException(
                status_code=400, detail=f"Payment processing error: {str(e)}"
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deleting payment method: {str(e)}")
            raise HTTPException(
                status_code=500, detail="Failed to delete payment method"
            )

    @staticmethod
    def handle_checkout_session(
        db, request, current_user
    ) -> CreateCheckoutSessionResponse:
        try:
            # Get subscription plan from database using plan_id as string
            plan = SubscriptionService.get_plan_by_id(db, int(request.plan_id))

            if not plan:
                raise HTTPException(
                    status_code=404, detail="Subscription plan not found"
                )

            # Get user details
            user = current_user

            # Use the price and currency from the request
            price = plan.price
            currency = plan.currency

            if currency == SubscriptionCurrencyType.USD.value:
                currency = "usd"
            elif currency == SubscriptionCurrencyType.JPY.value:
                currency = "jpy"

            logger.info(f"Request details - price: {price}, currency: {currency}")

            # Determine billing cycle for Stripe (default to monthly if not specified)
            price_interval = "month"  # You can modify this based on your needs

            # Create Stripe checkout session directly with price_data
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
                    payment_method_types=["card"],
                    line_items=[
                        {
                            "price_data": {
                                "currency": currency,
                                "product_data": {
                                    "name": plan.name,
                                    "description": "Subscription Plan Purchase",
                                },
                                "unit_amount": price,
                                "recurring": {"interval": price_interval},
                            },
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
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Internal server error: {str(e)}"
            )
