import logging
import os
from datetime import datetime

import stripe
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from studio.app.common.core.payment.payment_services import (
    PaymentService,
    SyncService,
    WebhookService,
)
from studio.app.common.db.database import get_db
from studio.app.common.schemas.payments import (
    PaymentSuccessRequest,
    PaymentSuccessResponse,
    SubscriptionStatusResponse,
    WebhookRequest,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Stripe setup
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

router = APIRouter(prefix="/api/payments", tags=["payments"])


# API Endpoints
@router.post("/stripe/success", response_model=PaymentSuccessResponse)
async def payment_success(
    request: PaymentSuccessRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Handle successful Stripe checkout completion.
    Creates or updates user subscription and records purchase.
    """
    try:
        # Process checkout using service
        result = PaymentService.process_payment_success(
            db=db,
            session_id=request.session_id,
            user_id=request.user_id,
            plan_id=request.plan_id,
        )

        # Validate that result has all required fields
        if not isinstance(result, dict) or "success" not in result:
            logger.error(f"Invalid result from process_payment_success: {result}")
            raise HTTPException(status_code=500, detail="Invalid processing result")

        # Add background task for syncing
        if result.get("subscription_user_id"):
            background_tasks.add_task(
                sync_subscription_background, result["subscription_user_id"]
            )

        return PaymentSuccessResponse(
            success=result["success"],
            message=result.get("message", "Subscription processed successfully"),
            subscription_user_id=result.get("subscription_user_id"),
            purchase_id=result.get("purchase_id"),
            expiration_date=result.get("expiration_date"),
        )

    except ValueError as e:
        logger.error(f"Validation error in checkout: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Checkout processing error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/stripe/webhook")
async def stripe_webhook(
    request: WebhookRequest,
    db: Session = Depends(get_db),
):
    """Handle Stripe webhooks for subscription events"""
    try:
        # Get raw body and signature header
        body = await request.body()
        sig_header = request.headers.get("stripe-signature")

        # Your webhook endpoint secret from Stripe Dashboard
        endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

        # Verify the webhook signature
        try:
            event = stripe.Webhook.construct_event(body, sig_header, endpoint_secret)
        except ValueError as e:
            logger.error("Invalid payload: " + str(e))
            raise HTTPException(status_code=400, detail="Invalid payload")
        except stripe.error.SignatureVerificationError as e:
            logger.error("Invalid signature: " + str(e))
            raise HTTPException(status_code=400, detail="Invalid signature")

        # Now use the verified event data
        event_type = event["type"]
        data = event["data"]["object"]

        if event_type == "checkout.session.completed":
            WebhookService.handle_checkout_completed(db, data)

        elif event_type == "invoice.payment_failed":
            WebhookService.handle_payment_failed(db, data)

        elif event_type == "customer.subscription.deleted":
            WebhookService.handle_subscription_cancelled(db, data)

        return {"received": True, "processed": event_type}

    except Exception as e:
        logger.error(f"Webhook processing error: {str(e)}")
        raise HTTPException(status_code=500, detail="Webhook processing failed")


@router.get("/subscription/status/{user_id}", response_model=SubscriptionStatusResponse)
async def get_subscription_status(user_id: int, db: Session = Depends(get_db)):
    """Get current subscription status for a user"""
    try:
        subscription_details = SyncService.get_subscription_status(db, user_id)

        return SubscriptionStatusResponse(
            user_id=user_id,
            has_active_subscription=subscription_details is not None,
            subscription_details=subscription_details,
        )

    except Exception as e:
        logger.error(f"Error getting subscription status: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow(),
        "service": "subscription-api",
    }


# Background Tasks
async def sync_subscription_background(subscription_user_id: int):
    """Background task wrapper for subscription syncing"""
    db = next(get_db())
    try:
        SyncService.sync_subscription_status(db, subscription_user_id)
    finally:
        db.close()
