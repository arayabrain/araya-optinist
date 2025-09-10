import os
from typing import List, Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

# Import your database models and dependencies
from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.subscription.subscription_controller import (
    SubscriptionCurrencyType,
    SubscriptionReader,
)
from studio.app.common.db.database import get_db
from studio.app.common.schemas.subscriptions import (
    CreateCheckoutSessionRequest,
    CreateCheckoutSessionResponse,
    PaymentMethodResponse,
    SubscriptionPlanResponse,
    UserSubscriptionResponse,
)
from studio.app.common.schemas.users import User

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])
logger = AppLogger.get_logger()


@router.get("/plans", response_model=List[SubscriptionPlanResponse])
def get_subscription_plans(db: Session = Depends(get_db)):
    try:
        plans = SubscriptionReader.get_active_plans(db)

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
    # Check if user can access this data (either own data or admin)
    if current_user.id != user_id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this user's subscription data",
        )

    try:
        # Get the most recent active subscription
        subscription = SubscriptionReader.get_user_subscription_plan(db, user_id)

        if not subscription:
            # Check if user has any expired subscriptions
            expired_subscription = SubscriptionReader.get_user_expired_subscription(
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
        plan = SubscriptionReader.get_plan_by_id(db, int(request.plan_id))
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
            # Get base URL from environment variables
            BASE_URL = os.getenv(
                "BASE_URL", "http://localhost:3000"
            )  # Default fallback

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
                    f"{BASE_URL}/console/account" "?session_id={CHECKOUT_SESSION_ID}"
                ),
                cancel_url=f"{BASE_URL}/console/subscription",
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


@router.get("/payment-methods/{user_id}", response_model=List[PaymentMethodResponse])
async def get_user_payment_methods(
    user_id: int,
    db: Session = Depends(get_db),
    # current_user: User = Depends(get_current_user),
):
    """
    Get user's payment methods with last 4 digits and card brand
    """
    # Check if user can access this data (either own data or admin)
    # if current_user.id != user_id and not current_user.is_admin:
    #     raise HTTPException(
    #         status_code=status.HTTP_403_FORBIDDEN,
    #         detail="Not authorized to access this user's payment methods",
    #     )

    try:
        # First, get the user's active subscription to find their Stripe customer ID
        subscription = (
            db.query(common_model.UserSubscription)
            .filter(
                and_(
                    common_model.UserSubscription.user_id == user_id,
                    common_model.UserSubscription.expiration > datetime.now(),
                )
            )
            .order_by(common_model.UserSubscription.expiration.desc())
            .first()
        )

        if not subscription:
            logger.info(f"No active subscription found for user {user_id}")
            return []

        # Get user email to find Stripe customer
        user = (
            db.query(common_model.User).filter(common_model.User.id == user_id).first()
        )
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        logger.info(
            f"Fetching payment methods for user {user_id} with email {user.email}"
        )

        # Find Stripe customer by email
        stripe_customers = stripe.Customer.list(email=user.email, limit=1)

        if not stripe_customers.data:
            logger.info(f"No Stripe customer found for user {user_id}")
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
            f"Stripe error when fetching payment methods for user {user_id}: {str(e)}"
        )
        raise HTTPException(
            status_code=400,
            detail=f"Failed to fetch payment methods from Stripe: {str(e)}",
        )
    except Exception as e:
        logger.error(f"Error fetching payment methods for user {user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch payment methods: {str(e)}",
        )


@router.get(
    "/payment-methods/{user_id}/default", response_model=Optional[PaymentMethodResponse]
)
async def get_user_default_payment_method(
    user_id: int,
    db: Session = Depends(get_db),
    # current_user: User = Depends(get_current_user),
):
    """
    Get user's default payment method
    """
    # Check if user can access this data (either own data or admin)
    # if current_user.id != user_id and not current_user.is_admin:
    #     raise HTTPException(
    #         status_code=status.HTTP_403_FORBIDDEN,
    #         detail="Not authorized to access this user's payment methods",
    #     )

    try:
        # Get user email to find Stripe customer
        user = (
            db.query(common_model.User).filter(common_model.User.id == user_id).first()
        )
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        logger.info(
            f"Fetching default pymt method for user {user_id} with email {user.email}"
        )

        # Find Stripe customer by email
        stripe_customers = stripe.Customer.list(email=user.email, limit=1)

        if not stripe_customers.data:
            logger.info(f"No Stripe customer found for user {user_id}")
            return None

        customer = stripe_customers.data[0]

        # Get default payment method
        default_pm_id = customer.invoice_settings.default_payment_method
        if not default_pm_id:
            logger.info(f"No default payment method set for user {user_id}")
            return None

        # Retrieve the payment method details
        payment_method = stripe.PaymentMethod.retrieve(default_pm_id)

        if payment_method.type != "card":
            logger.info(f"Default payment method is not a card for user {user_id}")
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
