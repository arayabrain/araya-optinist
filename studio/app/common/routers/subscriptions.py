from datetime import datetime
from typing import List, Optional

import stripe
from attrs import asdict
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.subscription.checkout_service import CheckoutService
from studio.app.common.core.subscription.subscription_service import (
    SubscriptionCurrencyType,
    SubscriptionService,
)
from studio.app.common.core.subscription.webhook_service import WebhookService
from studio.app.common.db.database import get_db
from studio.app.common.schemas.checkouts import CheckoutSessionRequest
from studio.app.common.schemas.subscriptions import (
    CancelSubscriptionResponse,
    CreateCheckoutSessionRequest,
    CreateCheckoutSessionResponse,
    CreateSetupIntentResponse,
    PaymentMethodResponse,
    SubscriptionPlanResponse,
    UpdatePaymentMethodResponse,
    UpdateSubscriptionRequest,
    UpdateSubscriptionResponse,
    UserSubscriptionResponse,
)
from studio.app.common.schemas.users import User
from enum import Enum

# Import your database models and dependencies


stripe.api_key = SubscriptionService.get_stripe_key()
STRIPE_CALLBACK_URL = SubscriptionService.get_base_url()

router = APIRouter(prefix="/api/subsc", tags=["Subscriptions"])
webhook_router = APIRouter(prefix="/api/subsc/webhooks", tags=["Subscription Webhooks"])
logger = AppLogger.get_logger()


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


@router.get("/mgmts/plans", response_model=List[SubscriptionPlanResponse])
def get_subscription_plans(db: Session = Depends(get_db)):
    try:
        plans = SubscriptionService.get_active_plans(db)

        if not plans:
            logger.warning("No subscription plans found")
            return []

        result: List[SubscriptionPlanResponse] = []
        for plan in plans:
            try:
                plan_response = SubscriptionPlanResponse(
                    id=plan.id,
                    name=plan.name,
                    price=plan.price,
                    billing_cycle=plan.billing_cycle,
                    features=plan.features,
                    currency=plan.currency,
                    status=plan.status,
                    created_at=plan.created_at,
                )
                result.append(plan_response)

            except Exception as plan_error:
                logger.error(f"Error processing plan {plan.id}: {plan_error}")
                # Skip this plan and continue with others
                continue

        return result

    except Exception as e:
        logger.error(f"Error fetching subscription plans: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch subscription plans: {str(e)}"
        )


@router.get(
    "/mgmts",
    response_model=Optional[UserSubscriptionResponse],
)
async def get_user_subscription(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get user's current active subscription
    """
    try:
        # Get the most recent active subscription
        subscription = SubscriptionService.get_user_subscription(db, current_user.id)
        logger.info(f"Fetched subscription for user {current_user.id}: {subscription}")

        if subscription is None:
            # Check if user has any expired subscriptions
            expired_subscription = SubscriptionService.get_user_expired_subscription(
                db, current_user.id
            )

            if expired_subscription:
                sub_data, plan_data, _ = expired_subscription
                return UserSubscriptionResponse(
                    id=sub_data.id,
                    user_id=sub_data.user_id,
                    plan_id=sub_data.plan_id,
                    created_at=sub_data.created_at,
                    updated_at=sub_data.updated_at,
                    plan_name=plan_data.name,
                    plan_price=plan_data.price,
                    expiration=sub_data.expiration,
                    is_expired=True,
                    scheduled_downgrade=sub_data.scheduled_downgrade,
                )

            return None

        # If we get here, result is not None, so we can safely unpack
        subscription, subscription_plans = subscription
        sub_data, plan_data = subscription, subscription_plans
        return UserSubscriptionResponse(
            id=sub_data.id,
            user_id=sub_data.user_id,
            plan_id=sub_data.plan_id,
            created_at=sub_data.created_at,
            updated_at=sub_data.updated_at,
            plan_name=plan_data.name,
            plan_price=plan_data.price,
            expiration=sub_data.expiration,
            is_expired=False,
            scheduled_downgrade=sub_data.scheduled_downgrade,
        )
    except Exception as e:
        logger.error(
            f"Error fetching subscription for user {current_user.id}: {str(e)}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch user subscription: {str(e)}",
        )


@router.put("/mgmts", response_model=UpdateSubscriptionResponse)
async def update_user_subscription(
    request: UpdateSubscriptionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update user's subscription to a different plan - webhook-driven database updates
    """
    try:
        # Get the new plan details
        new_plan = SubscriptionService.get_plan_by_id(db, request.new_plan_id)
        if not new_plan:
            raise HTTPException(
                status_code=404, detail="New subscription plan not found"
            )

        # Get current user subscription
        current_subscription_result = SubscriptionService.get_user_subscription(
            db, current_user.id
        )
        if not current_subscription_result:
            raise HTTPException(
                status_code=404,
                detail="No active subscription found. Please create a new subscription",
            )

        sub_data, current_plan = current_subscription_result

        # Check if user is trying to "update" to the same plan
        if current_plan.id == new_plan.id:
            raise HTTPException(
                status_code=400, detail="User is already subscribed to this plan"
            )

        # Get Stripe customer
        customer = await _get_stripe_customer_by_email(current_user.email)
        if not customer:
            raise HTTPException(
                status_code=404, detail="No Stripe customer found for user"
            )

        # Get active Stripe subscription
        stripe_subscriptions = stripe.Subscription.list(
            customer=customer.id, status=StripeSubscriptionStatus.ACTIVE, limit=1
        )

        if not stripe_subscriptions.data:
            raise HTTPException(
                status_code=404, detail="No active Stripe subscription found"
            )

        stripe_subscription = stripe_subscriptions.data[0]

        # Prepare currency for Stripe
        currency = new_plan.currency
        if currency == SubscriptionCurrencyType.USD.value:
            currency = "usd"
        elif currency == SubscriptionCurrencyType.JPY.value:
            currency = "jpy"

        # Create new price in Stripe for the new plan
        stripe_price = stripe.Price.create(
            currency=currency,
            unit_amount=new_plan.price,
            recurring={"interval": "month"},
            product_data={
                "name": new_plan.name,
                "metadata": {"plan_id": str(new_plan.id)},
            },
        )

        logger.info(
            f"Processing scheduled subscription change for user {current_user.id}"
        )

        # Get the current period end from the subscription items
        current_period_end = stripe_subscription["items"]["data"][0][
            "current_period_end"
        ]

        logger.info(f"Current period end timestamp: {current_period_end}")
        logger.info(
            f"Current period end date: {datetime.fromtimestamp(current_period_end)}"
        )

        # Schedule change at period end using proper Stripe schedules
        current_period_end = stripe_subscription["items"]["data"][0][
            "current_period_end"
        ]

        # Cancel current subscription at period end
        stripe.Subscription.modify(stripe_subscription.id, cancel_at_period_end=True)

        # Create a new subscription schedule that starts when current ends
        stripe.SubscriptionSchedule.create(
            customer=stripe_subscription.customer,
            start_date=current_period_end,  # Start when current subscription ends
            phases=[
                {
                    # New phase - switch to new plan
                    "items": [
                        {
                            "price": stripe_price.id,
                            "quantity": 1,
                        }
                    ],
                    # This phase continues indefinitely
                },
            ],
            metadata={
                "user_id": str(current_user.id),
                "new_plan_id": str(new_plan.id),
                "old_plan_id": str(current_plan.id),
                "scheduled_change": "true",
            },
        )

        change_date = datetime.fromtimestamp(current_period_end)
        message = (
            f"Subscription will change to {new_plan.name} on "
            f"{change_date.strftime('%Y-%m-%d')}"
        )
        effective_date = int(current_period_end)

        # Database will be updated via subscription_schedule.updated and
        # subscription_schedule.released webhooks

        # Determine if this is an upgrade or downgrade
        plan_change_type = (
            "upgrade" if new_plan.price > current_plan.price else "downgrade"
        )

        logger.info(
            f"subscription change for user {current_user.id} "
            f"from plan {current_plan.id} to plan {new_plan.id} ({plan_change_type})"
        )

        return UpdateSubscriptionResponse(
            success=True,
            message=message,
            old_plan_name=current_plan.name,
            new_plan_name=new_plan.name,
            change_type=plan_change_type,
            effective_date=effective_date,
            next_billing_date=int(current_period_end),
            prorated_amount=("Check latest invoice for proration details"),
        )

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error updating subscription: {str(e)}")
        raise HTTPException(
            status_code=400, detail=f"Payment processing error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error updating subscription for user {current_user.id}: {str(e)}"
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to update subscription: {str(e)}"
        )


@router.delete("/mgmts/cancel", response_model=CancelSubscriptionResponse)
async def cancel_user_subscription(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Cancel user's subscription at period end
    - Subscription will be cancelled when current billing period ends
    - User retains access until then
    - Database updates handled via webhook
    """
    try:

        # Get current user subscription
        current_subscription_result = SubscriptionService.get_user_subscription(
            db, current_user.id
        )
        if not current_subscription_result:
            raise HTTPException(
                status_code=404, detail="No active subscription found to cancel"
            )

        sub_data, current_plan = current_subscription_result

        # Get Stripe customer
        customer = await _get_stripe_customer_by_email(current_user.email)
        if not customer:
            raise HTTPException(
                status_code=404, detail="No Stripe customer found for user"
            )

        # Get active Stripe subscription
        stripe_subscriptions = stripe.Subscription.list(
            customer=customer.id, status=StripeSubscriptionStatus.ACTIVE, limit=1
        )

        if not stripe_subscriptions.data:
            raise HTTPException(
                status_code=404, detail="No active Stripe subscription found"
            )

        stripe_subscription = stripe_subscriptions.data[0]

        logger.info(f"Scheduling cancellation at period end for user {current_user.id}")

        current_period_end = stripe_subscription["items"]["data"][0][
            "current_period_end"
        ]

        # Handle existing schedule if present
        existing_schedule_id = stripe_subscription.get("schedule")
        if existing_schedule_id:
            try:
                # Cancel any existing schedule
                stripe.SubscriptionSchedule.cancel(existing_schedule_id)
                logger.info(f"Cancelled existing schedule: {existing_schedule_id}")

                # Get the subscription again after cancelling schedule
                stripe_subscription = stripe.Subscription.retrieve(
                    stripe_subscription.id
                )
            except Exception as e:
                logger.warning(f"Could not cancel schedule: {e}")

        # Set subscription to cancel at period end
        stripe.Subscription.modify(
            stripe_subscription.id,
            cancel_at_period_end=True,
            metadata={
                **stripe_subscription.metadata,
                "cancellation_requested": "true",
                "cancellation_requested_at": str(int(datetime.utcnow().timestamp())),
            },
        )

        SubscriptionService.update_scheduled_downgrade(db, current_user.id, True)

        # Database will be updated via customer.subscription.updated webhook

        access_until_date = datetime.fromtimestamp(current_period_end)
        message = (
            f"Subscription will be cancelled on "
            f"{access_until_date.strftime('%Y-%m-%d')}. "
            f"You will retain access until then."
        )

        logger.info(
            f"Successfully scheduled cancellation for user {current_user.id} "
            f"at period end"
        )

        return CancelSubscriptionResponse(
            success=True,
            message=message,
            cancellation_date=access_until_date.strftime("%Y-%m-%d"),
            access_until=access_until_date.strftime("%Y-%m-%d %H:%M:%S"),
        )

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error cancelling subscription: {str(e)}")
        raise HTTPException(
            status_code=400, detail=f"Payment processing error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error cancelling subscription for user {current_user.id}: {str(e)}"
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to cancel subscription: {str(e)}"
        )


@router.get("/payment-methods", response_model=List[PaymentMethodResponse])
async def get_user_payment_methods(
    current_user: User = Depends(get_current_user),
):
    """
    Get user's payment methods with last 4 digits and card brand
    """
    try:
        # Get user email to find Stripe customer
        user = current_user
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        logger.info(
            f"Fetching payment methods for user {user.id} with email {user.email}"
        )

        # Find Stripe customer by email
        stripe_customers = stripe.Customer.list(email=user.email, limit=1)

        if not stripe_customers.data:
            logger.info(f"No Stripe customer found for user {user.email}")
            return []

        customer = stripe_customers.data[0]

        # Get all payment methods for this customer
        payment_methods = stripe.PaymentMethod.list(customer=customer.id, type="card")

        result = []
        for pm in payment_methods.data:
            card = pm.card
            payment_method_response = PaymentMethodResponse(
                id=pm.id,
                last4=card.last4,
                brand=card.brand,
                exp_month=card.exp_month,
                exp_year=card.exp_year,
                is_default=pm.id == customer.invoice_settings.default_payment_method,
            )
            result.append(payment_method_response)

        return result

    except stripe.error.StripeError as e:
        logger.error(
            f"Stripe error when fetching payment methods for user {user.id}: {str(e)}"
        )
        raise HTTPException(
            status_code=400,
            detail=f"Failed to fetch payment methods from Stripe: {str(e)}",
        )
    except Exception as e:
        logger.error(f"Error fetching payment methods for user {user.id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch payment methods: {str(e)}",
        )


@router.get("/payment-methods/default", response_model=Optional[PaymentMethodResponse])
async def get_user_default_payment_method(
    current_user: User = Depends(get_current_user),
):
    """
    Get user's default payment method
    """
    try:
        # Get user email to find Stripe customer
        user = current_user
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        logger.info(
            f"Fetching default pymt method for user {user.id} with email {user.email}"
        )

        # Find Stripe customer by email
        stripe_customer = await _get_stripe_customer_by_email(user.email)

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


@router.post("/payment-methods/setup-intent", response_model=CreateSetupIntentResponse)
async def create_setup_intent(
    current_user: User = Depends(get_current_user),
):
    """
    Create a SetupIntent for collecting payment method information
    """
    try:
        user = current_user

        # Get or create Stripe customer
        customer = await _get_stripe_customer_by_email(user.email)

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


@router.put("/payment-methods", response_model=UpdatePaymentMethodResponse)
async def update_default_payment_method(
    payment_method_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Update the default payment method for a user's subscription
    """
    try:
        user = current_user

        # Get Stripe customer
        customer = await _get_stripe_customer_by_email(user.email)
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
        raise HTTPException(status_code=500, detail="Failed to update payment method")


@router.delete("/payment-methods/{payment_method_id}")
async def delete_payment_method(
    payment_method_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Delete a payment method (cannot delete if it's the default for active subscriptions)
    """
    try:
        user = current_user

        # Get Stripe customer
        customer = await _get_stripe_customer_by_email(user.email)
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

        # logger.info(f"Deleted payment method {payment_method_id} for user {user.id}")

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
        raise HTTPException(status_code=500, detail="Failed to delete payment method")


@router.post(
    "/checkout/create-checkout-session", response_model=CreateCheckoutSessionResponse
)
async def create_checkout_session(
    request: CreateCheckoutSessionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a Stripe checkout session for subscription"""
    try:
        # Get subscription plan from database using plan_id as string
        logger.info(f"Creating checkout session for plan_id: {request.plan_id}")
        plan = SubscriptionService.get_plan_by_id(db, int(request.plan_id))
        logger.info(f"Retrieved plan: {plan}")

        if not plan:
            raise HTTPException(status_code=404, detail="Subscription plan not found")

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
            subscription_account = CheckoutService.get_subscription_account(db, user.id)
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
                status_code=400, detail=f"Failed to create checkout session: {str(e)}"
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/checkout/validate-checkout-session", response_model=bool)
async def validate_checkout_session(
    request: CheckoutSessionRequest,
):
    """
    Validate a Stripe checkout session ID
    """
    try:
        # Retrieve the session from Stripe
        session = stripe.checkout.Session.retrieve(request.session_id)

        # Check if the session is complete and paid
        if session.payment_status == "paid" and session.status == "complete":
            return True
        else:
            return False

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error validating checkout session: {str(e)}")
        raise HTTPException(
            status_code=400, detail=f"Failed to validate checkout session: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Error validating checkout session: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/checkout/failed-checkout-session", response_model=bool)
async def validate_failed_checkout_session(
    request: CheckoutSessionRequest,
):
    """
    Validate a Stripe checkout session ID for FAILED page
    Returns True if session exists and is in a failed/incomplete state
    """
    try:
        # Retrieve the session from Stripe
        session = stripe.checkout.Session.retrieve(request.session_id)

        # Check if the session exists and is in a legitimate failed state
        # Valid failed states: expired, open with unpaid status
        if session.status == "expired" or (
            session.status == "open" and session.payment_status == "unpaid"
        ):
            return True
        else:
            return False

    except stripe.error.InvalidRequestError:
        # Session doesn't exist or is invalid
        return False
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error validating failed checkout session: {str(e)}")
        raise HTTPException(
            status_code=400, detail=f"Failed to validate checkout session: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Error validating failed checkout session: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@webhook_router.post("/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle Stripe webhooks for subscription events"""
    try:
        # Get raw body and signature header
        body = await request.body()
        sig_header = request.headers.get("stripe-signature")

        logger.info(f"Webhook received - Body length: {len(body)}")
        logger.info(f"Signature header: {sig_header[:50] if sig_header else 'None'}...")

        # Your webhook endpoint secret from Stripe Dashboard
        endpoint_secret = WebhookService.get_webhook_secret()

        secret_display = "***" + endpoint_secret[-4:] if endpoint_secret else "None"
        logger.info(f"Using webhook secret: {secret_display}")

        # Verify the webhook signature
        try:
            event = stripe.Webhook.construct_event(body, sig_header, endpoint_secret)
            logger.info("Webhook signature verified successfully")
        except ValueError as e:
            logger.error(f"Invalid payload: {str(e)}")
            raise HTTPException(status_code=400, detail="Invalid payload")
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Invalid signature: {str(e)}")
            raise HTTPException(status_code=400, detail="Invalid signature")

        # Now use the verified event data
        event_type = event["type"]
        data = event["data"]["object"]

        logger.info(f"Processing event type: {event_type}")

        if event_type == "checkout.session.completed":
            logger.info("Handling checkout.session.completed")
            WebhookService.handle_checkout_completed(db, data)

        elif event_type == "invoice.payment_failed":
            logger.info("Handling invoice.payment_failed")
            WebhookService.handle_payment_failed(db, data)

        elif event_type == "customer.subscription.deleted":
            logger.info("Handling customer.subscription.deleted")
            WebhookService.handle_subscription_cancelled(db, data)

        elif event_type == "subscription_schedule.released":
            logger.info("Handling subscription_schedule.released")
            WebhookService.handle_subscription_schedule_released(db, data)

        elif event_type == "invoice.payment_succeeded":
            logger.info("Handling invoice.payment_succeeded")
            WebhookService.handle_subscription_payment_succeeded(db, data)

        else:
            logger.info(f"Unhandled webhook event type: {event_type}")

        logger.info(f"Successfully processed {event_type}")
        return {"received": True, "processed": event_type}

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Webhook processing error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Webhook processing failed")


async def _get_stripe_customer_by_email(email: str) -> Optional[stripe.Customer]:
    """Get Stripe customer by email"""
    try:
        stripe_customers = stripe.Customer.list(email=email, limit=1)
        return stripe_customers.data[0] if stripe_customers.data else None
    except stripe.error.StripeError as e:
        logger.error(f"Error fetching Stripe customer: {str(e)}")
        return None
