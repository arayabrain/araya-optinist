from typing import List, Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

# Import your database models and dependencies
from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.subscription.subscription_service import (
    SubscriptionCurrencyType,
    SubscriptionService,
)
from studio.app.common.db.database import get_db
from studio.app.common.schemas.subscriptions import (
    CreateCheckoutSessionRequest,
    CreateCheckoutSessionResponse,
    CreateSetupIntentResponse,
    PaymentMethodResponse,
    SubscriptionPlanResponse,
    UpdatePaymentMethodResponse,
    UserSubscriptionResponse,
)
from studio.app.common.schemas.users import User

stripe.api_key = SubscriptionService.get_stripe_key()
STRIPE_CALLBACK_URL = SubscriptionService.get_base_url()

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])
logger = AppLogger.get_logger()


@router.get("/plans", response_model=List[SubscriptionPlanResponse])
def get_subscription_plans(db: Session = Depends(get_db)):
    try:
        plans = SubscriptionService.get_active_plans(db)

        if not plans:
            logger.info("No subscription plans found")
            return []

        result = []
        for plan in plans:
            try:
                # Create response object - let Pydantic validators handle conversion
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
    "/user/{user_id}",
    response_model=Optional[UserSubscriptionResponse],
)
async def get_user_subscription(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get user's current active subscription
    """
    try:
        # Get the most recent active subscription
        subscription = SubscriptionService.get_user_subscription_plan(db, user_id)

        if not subscription:
            # Check if user has any expired subscriptions
            expired_subscription = SubscriptionService.get_user_expired_subscription(
                db, user_id
            )

            if expired_subscription:
                sub_data, plan_data = expired_subscription
                return UserSubscriptionResponse(
                    id=sub_data.id,
                    plan_id=sub_data.plan_id,
                    user_id=sub_data.user_id,
                    expiration=sub_data.expiration,
                    plan_name=plan_data.name,
                    plan_price=plan_data.price,
                    created_at=sub_data.created_at,
                    updated_at=sub_data.updated_at,
                )

            return None

        sub_data, plan_data = subscription
        return UserSubscriptionResponse(
            id=sub_data.id,
            plan_id=sub_data.plan_id,
            user_id=sub_data.user_id,
            expiration=sub_data.expiration,
            plan_name=plan_data.name,
            plan_price=plan_data.price,
            created_at=sub_data.created_at,
            updated_at=sub_data.updated_at,
        )

    except Exception as e:
        logger.error(f"Error fetching subscription for user {user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch user subscription: {str(e)}",
        )


@router.post("/create-checkout-session", response_model=CreateCheckoutSessionResponse)
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
                            "unit_amount": price,  # Price in cents from request
                            "recurring": {"interval": price_interval},
                        },
                        "quantity": 1,
                    }
                ],
                mode="subscription",
                success_url=(
                    f"{STRIPE_CALLBACK_URL}/console/account"
                    "?session_id={CHECKOUT_SESSION_ID}"
                ),
                cancel_url=f"{STRIPE_CALLBACK_URL}/console/subscription",
                client_reference_id=str(user.id),
                customer_email=user.email,
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


@router.get(
    "/payment-methods/{user_id}/default", response_model=Optional[PaymentMethodResponse]
)
async def get_user_default_payment_method(
    user_id: int,
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
            f"Fetching default pymt method for user {user_id} with email {user.email}"
        )

        # Find Stripe customer by email
        stripe_customers = _get_stripe_customer_by_email(user.email)

        if not stripe_customers.data:
            logger.info(f"No Stripe customer found for user {user.id}")
            return None

        customer = stripe_customers.data[0]

        # Get default payment method
        default_pm_id = customer.invoice_settings.default_payment_method
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
            f"{user_id}: {str(e)}"
        )
        raise HTTPException(
            status_code=400,
            detail=f"Failed to fetch default payment method from Stripe: {str(e)}",
        )
    except Exception as e:
        logger.error(
            f"Error fetching default payment method for user {user_id}: {str(e)}"
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
        subscriptions = stripe.Subscription.list(customer=customer.id, status="active")

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
        subscriptions = stripe.Subscription.list(customer=customer.id, status="active")

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


async def _get_stripe_customer_by_email(email: str) -> Optional[stripe.Customer]:
    """Get Stripe customer by email"""
    try:
        stripe_customers = stripe.Customer.list(email=email, limit=1)
        return stripe_customers.data[0] if stripe_customers.data else None
    except stripe.error.StripeError as e:
        logger.error(f"Error fetching Stripe customer: {str(e)}")
        return None
