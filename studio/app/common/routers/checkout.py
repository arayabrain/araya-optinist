from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional, Dict, Any
import stripe
import os
import logging

from studio.app.common.core.checkout.checkout_services import (
    CheckoutService,
    WebhookService,
    SyncService,
)
from studio.app.common.db.database import get_db

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Stripe setup
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

router = APIRouter(prefix="/api/v1/checkout", tags=["checkout"])


# Pydantic Models
class CheckoutSuccessRequest(BaseModel):
    session_id: str = Field(..., description="Stripe checkout session ID")
    user_id: int = Field(..., description="Internal user ID")
    plan_id: int = Field(..., description="Subscription plan ID (1=Free, 2=Premium)")


class CheckoutSuccessResponse(BaseModel):
    success: bool
    message: str
    subscription_user_id: Optional[int] = None
    purchase_id: Optional[int] = None
    expiration_date: Optional[datetime] = None


class WebhookRequest(BaseModel):
    event_type: str
    data: Dict[str, Any]


class SubscriptionStatusResponse(BaseModel):
    user_id: int
    has_active_subscription: bool
    subscription_details: Optional[Dict[str, Any]] = None


# API Endpoints
@router.post("/stripe/checkout-success", response_model=CheckoutSuccessResponse)
async def checkout_success(
    request: CheckoutSuccessRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Handle successful Stripe checkout completion.
    Creates or updates user subscription and records purchase.
    """
    try:
        # Process checkout using service
        result = CheckoutService.process_checkout_success(
            db=db,
            session_id=request.session_id,
            user_id=request.user_id,
            plan_id=request.plan_id,
        )

        # Add background task for syncing
        background_tasks.add_task(
            sync_subscription_background, result["subscription_user_id"]
        )

        return CheckoutSuccessResponse(
            success=result["success"],
            message=result["message"],
            subscription_user_id=result["subscription_user_id"],
            purchase_id=result["purchase_id"],
            expiration_date=result["expiration_date"],
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Checkout processing error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/stripe/webhook")
async def stripe_webhook(
    request: WebhookRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Handle Stripe webhooks for subscription events"""
    try:
        event_type = request.event_type
        data = request.data

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
