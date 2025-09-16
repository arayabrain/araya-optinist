from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel
from sqlmodel import Field


class PaymentSuccessRequest(BaseModel):
    session_id: str = Field(..., description="Stripe checkout session ID")
    user_id: int = Field(..., description="Internal user ID")
    plan_id: int = Field(..., description="Subscription plan ID (1=Free, 2=Premium)")


class PaymentSuccessResponse(BaseModel):
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
