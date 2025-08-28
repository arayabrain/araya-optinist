from datetime import datetime
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_
from sqlalchemy.orm import Session
import stripe

# Import your database models and dependencies
from studio.app.common import models as common_model
from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.logger import AppLogger
from studio.app.common.db.database import get_db
from studio.app.common.models.subscription import SubscriptionPlans
from studio.app.common.schemas.subscriptions import (
    CreateCheckoutSessionRequest,
    CreateCheckoutSessionResponse,
    SubscriptionPlanResponse,
    UserSubscriptionResponse,
)
from studio.app.common.schemas.users import User

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])
logger = AppLogger.get_logger()

# Enum for Subscription Status
SUBSCRIPTION_ACTIVE_STATUS = {
    "ACTIVE": "1",
    "INACTIVE": "0",
}


@router.get("/plans", response_model=List[SubscriptionPlanResponse])
def get_subscription_plans(db: Session = Depends(get_db)):
    try:
        # Query active plans
        plans = (
            db.query(SubscriptionPlans)
            .filter(SubscriptionPlans.status == SUBSCRIPTION_ACTIVE_STATUS["ACTIVE"])
            .all()
        )

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
        subscription = (
            db.query(common_model.UserSubscription, common_model.SubscriptionPlans)
            .join(
                common_model.SubscriptionPlans,
                common_model.UserSubscription.plan_id
                == common_model.SubscriptionPlans.id,
            )
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
            # Check if user has any expired subscriptions
            expired_subscription = (
                db.query(common_model.UserSubscription, common_model.SubscriptionPlans)
                .join(
                    common_model.SubscriptionPlans,
                    common_model.UserSubscription.plan_id
                    == common_model.SubscriptionPlans.id,
                )
                .filter(common_model.UserSubscription.user_id == user_id)
                .order_by(common_model.UserSubscription.expiration.desc())
                .first()
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
        plan = (
            db.query(SubscriptionPlans)
            .filter(
                SubscriptionPlans.id == request.plan_id,
            )
            .first()
        )
        logger.info(f"Retrieved plan: {plan}")

        if not plan:
            raise HTTPException(status_code=404, detail="Subscription plan not found")

        # Get user details
        user = current_user

        # Use the price and currency from the request
        price = plan.price
        currency = plan.currency

        if currency == 1:
            currency = "usd"
        elif currency == 2:
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
                success_url="http://localhost:3000/console/account",
                cancel_url="http://localhost:3000/console/subscription",
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
