"""
Stripe Router
Clean router file with minimal business logic - delegates to stripe_services
"""

from fastapi import APIRouter, HTTPException, status, Request, Depends
from pydantic import BaseModel, validator
from typing import Dict, Optional, List
import stripe
import os
from sqlalchemy.orm import Session

# Import your database dependencies and schemas
from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.logger import AppLogger
from studio.app.common.db.database import get_db
from studio.app.common.schemas.users import User

# Import ALL service functions from stripe_services
from studio.app.common.core.subscriptions.stripe_services import (
    # Database operations
    get_plan_from_db,
    get_or_create_user_account,
    create_user_subscription,
    create_purchase_record,
    create_cancellation_record,
    get_user_stripe_customer_id,
    find_recent_purchase,
    # Business logic
    calculate_amount_from_plan,
    calculate_expiration_date,
    format_plan_display,
    format_plan_description,
    # Stripe operations
    get_or_create_stripe_customer,
    create_stripe_payment_intent,
    create_stripe_subscription,
    cancel_stripe_subscription,
    get_stripe_payment_methods,
    # Webhook handling
    verify_webhook_signature,
    handle_payment_intent_succeeded,
    handle_invoice_payment_failed,
    handle_subscription_deleted,
    handle_subscription_updated,
    # Validation
    validate_plan_exists,
    validate_subscription_ownership,
    validate_stripe_price_configured,
    # Exceptions
    PlanValidationError,
    SubscriptionError,
)

# Configure logging
logger = AppLogger.get_logger()

router = APIRouter(prefix="/stripe", tags=["stripe"])

# ============ REQUEST/RESPONSE MODELS ONLY ============


class PaymentIntentRequest(BaseModel):
    plan_id: int
    currency: str = "usd"
    customer_email: Optional[str] = None
    customer_id: Optional[str] = None
    metadata: Optional[Dict[str, str]] = None

    @validator("currency")
    def validate_currency(cls, v):
        if v.lower() not in ["usd", "eur", "gbp"]:
            raise ValueError("Currency must be USD, EUR, or GBP")
        return v.lower()


class PaymentIntentResponse(BaseModel):
    client_secret: str
    payment_intent_id: str
    amount: int
    currency: str
    plan_details: Dict[str, str]


class CreateSubscriptionRequest(BaseModel):
    plan_id: int
    customer_email: Optional[str] = None
    payment_method_id: str
    metadata: Optional[Dict[str, str]] = None


class UpdateSubscriptionRequest(BaseModel):
    subscription_id: str
    plan_id: Optional[int] = None


class CancelSubscriptionRequest(BaseModel):
    subscription_id: str
    cancel_at_period_end: bool = True
    reason: Optional[str] = "user_request"
    notes: Optional[str] = None


class PaymentMethodResponse(BaseModel):
    id: str
    type: str
    card: Optional[Dict] = None
    created: int


class SubscriptionResponse(BaseModel):
    id: str
    status: str
    current_period_start: int
    current_period_end: int
    plan_id: int
    plan_name: str
    amount: int
    currency: str


# ============ UTILITY DECORATOR ============


def handle_exceptions(func):
    """Decorator to handle common exceptions"""

    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except (PlanValidationError, SubscriptionError) as e:
            logger.error(f"Validation error: {e.message}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=e.message
            )
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Payment service error",
            )
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred",
            )

    return wrapper


# ============ PAYMENT ENDPOINTS ============
@router.post("/payment/create-intent", response_model=PaymentIntentResponse)
@handle_exceptions
async def create_payment_intent(
    request: PaymentIntentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a Stripe payment intent for plan purchase"""
    logger.info(
        f"Creating payment intent for user {current_user.id} - "
        f"plan ID: {request.plan_id}"
    )

    # Validate plan using service
    plan = validate_plan_exists(db, request.plan_id)
    amount = calculate_amount_from_plan(plan)

    # Prepare metadata
    metadata = {
        "plan_id": str(plan.id),
        "plan_name": plan.name,
        "billing_cycle": str(plan.billing_cycle),
        "user_id": str(current_user.id),
    }
    if request.metadata:
        metadata.update(request.metadata)

    # Handle customer using service
    customer_id = request.customer_id
    if not customer_id and request.customer_email:
        customer_id = get_or_create_stripe_customer(
            request.customer_email, current_user.id
        )
        get_or_create_user_account(db, current_user.id, customer_id)

    # Create payment intent using service
    payment_intent = create_stripe_payment_intent(
        amount, request.currency, metadata, customer_id
    )

    return PaymentIntentResponse(
        client_secret=payment_intent.client_secret,
        payment_intent_id=payment_intent.id,
        amount=amount,
        currency=request.currency,
        plan_details={
            "plan_id": str(plan.id),
            "plan_name": plan.name,
            "billing_cycle": str(plan.billing_cycle),
            "description": format_plan_description(plan),
            "amount_display": f"${amount/100:.2f}",
        },
    )


@router.get("/payment/intent/{payment_intent_id}")
async def get_payment_intent_status(
    payment_intent_id: str, current_user: User = Depends(get_current_user)
):
    """Get the status of a payment intent"""
    try:
        payment_intent = stripe.PaymentIntent.retrieve(payment_intent_id)

        # Use service validation function
        if not validate_subscription_ownership(
            payment_intent.metadata, current_user.id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
            )

        return {
            "payment_intent_id": payment_intent.id,
            "status": payment_intent.status,
            "amount": payment_intent.amount,
            "currency": payment_intent.currency,
            "plan_id": payment_intent.metadata.get("plan_id"),
            "plan_name": payment_intent.metadata.get("plan_name"),
            "billing_cycle": payment_intent.metadata.get("billing_cycle"),
        }

    except stripe.error.InvalidRequestError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment intent not found"
        )
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Payment service error",
        )


# ============ SUBSCRIPTION ENDPOINTS ============


@router.post("/subscription/create", response_model=SubscriptionResponse)
@handle_exceptions
async def create_subscription(
    request: CreateSubscriptionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new subscription"""
    logger.info(
        f"Creating subscription for user {current_user.id} - plan ID: {request.plan_id}"
    )

    # Validate plan and get Stripe price ID using services
    plan = validate_plan_exists(db, request.plan_id)
    stripe_price_id = validate_stripe_price_configured(plan)

    # Handle customer using service
    customer_email = request.customer_email or getattr(current_user, "email", None)
    if not customer_email:
        raise SubscriptionError("Customer email is required")

    customer_id = get_or_create_stripe_customer(customer_email, current_user.id)
    get_or_create_user_account(db, current_user.id, customer_id)

    # Prepare metadata
    metadata = {
        "user_id": str(current_user.id),
        "plan_id": str(plan.id),
        "plan_name": plan.name,
        "billing_cycle": str(plan.billing_cycle),
    }
    if request.metadata:
        metadata.update(request.metadata)

    # Create Stripe subscription using service
    subscription = create_stripe_subscription(
        customer_id, stripe_price_id, request.payment_method_id, metadata
    )

    # Create database records using services
    expiration = calculate_expiration_date(plan)
    create_user_subscription(db, current_user.id, plan.id, expiration)
    create_purchase_record(db, current_user.id, plan.id)

    return SubscriptionResponse(
        id=subscription.id,
        status=subscription.status,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        plan_id=plan.id,
        plan_name=plan.name,
        amount=calculate_amount_from_plan(plan),
        currency="usd",
    )


@router.delete("/subscription/cancel")
@handle_exceptions
async def cancel_subscription(
    request: CancelSubscriptionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel a subscription"""
    # Verify ownership using service
    subscription = stripe.Subscription.retrieve(request.subscription_id)
    if not validate_subscription_ownership(subscription.metadata, current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )

    # Cancel subscription using service
    subscription = cancel_stripe_subscription(
        request.subscription_id, request.cancel_at_period_end
    )

    # Create cancellation record using service
    plan_id = int(subscription.metadata.get("plan_id", 0))
    if plan_id:
        recent_purchase = find_recent_purchase(db, current_user.id, plan_id)
        if recent_purchase:
            create_cancellation_record(
                db, current_user.id, recent_purchase.id, request.reason, request.notes
            )

    return {
        "subscription_id": subscription.id,
        "status": subscription.status,
        "canceled_at": subscription.canceled_at,
        "cancel_at_period_end": subscription.cancel_at_period_end,
        "current_period_end": subscription.current_period_end,
    }


@router.get("/payment-methods", response_model=List[PaymentMethodResponse])
async def get_payment_methods(
    current_user: User = Depends(get_current_user),
    customer_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Get customer's payment methods"""
    try:
        # Get customer ID using service
        if not customer_id:
            customer_id = get_user_stripe_customer_id(db, current_user.id)
            if not customer_id:
                return []

        # Verify customer belongs to user using service
        customer = stripe.Customer.retrieve(customer_id)
        if not validate_subscription_ownership(customer.metadata, current_user.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
            )

        # Get payment methods using service
        payment_methods = get_stripe_payment_methods(customer_id)

        return [
            PaymentMethodResponse(
                id=pm.id,
                type=pm.type,
                card=pm.card.to_dict() if pm.card else None,
                created=pm.created,
            )
            for pm in payment_methods
        ]

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Payment service error",
        )
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred",
        )


# ============ WEBHOOK ENDPOINT ============


@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle Stripe webhook events"""
    try:
        payload = await request.body()
        signature = request.headers.get("stripe-signature")

        # Use service function for verification
        if not verify_webhook_signature(payload, signature):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature"
            )

        event = stripe.Webhook.construct_event(
            payload, signature, os.getenv("STRIPE_WEBHOOK_SECRET")
        )
        logger.info(f"Received webhook event: {event['type']}")

        # Route to appropriate service handler
        event_handlers = {
            "payment_intent.succeeded": handle_payment_intent_succeeded,
            "invoice.payment_failed": handle_invoice_payment_failed,
            "customer.subscription.deleted": handle_subscription_deleted,
            "customer.subscription.updated": handle_subscription_updated,
        }

        handler = event_handlers.get(event["type"])
        if handler:
            await handler(event["data"]["object"], db)
        else:
            logger.info(f"Unhandled event type: {event['type']}")

        return {"status": "success"}

    except Exception as e:
        logger.error(f"Webhook error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Webhook processing failed"
        )


# ============ UTILITY ENDPOINTS ============


@router.get("/plans")
async def get_available_plans(db: Session = Depends(get_db)):
    """Get all available plans from database"""
    try:

        plans = get_plan_from_db(db)

        result = []
        for plan in plans:
            result.append(
                {
                    "id": plan.id,
                    "name": plan.name,
                    "price": plan.price,
                    "billing_cycle": plan.billing_cycle,
                    "features": plan.features,
                    "currency": plan.currency,
                    "amount_cents": calculate_amount_from_plan(
                        plan
                    ),  # Use service function
                    "display": format_plan_display(plan),  # Use service function
                }
            )

        return {"plans": result}

    except Exception as e:
        logger.error(f"Error fetching plans: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch plans",
        )


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "stripe-router",
        "stripe_configured": bool(stripe.api_key),
        "webhook_configured": bool(os.getenv("STRIPE_WEBHOOK_SECRET")),
    }
