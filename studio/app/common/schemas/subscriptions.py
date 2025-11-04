import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, validator


class SubscriptionPlanResponse(BaseModel):
    id: int
    name: str
    price: int
    billing_cycle: int = Field(..., description="1=Monthly, 2=Annual")
    features: Dict[str, List[Dict[str, Any]]] = Field(
        ..., description="JSON features data"
    )
    currency: int = Field(..., description="1=USD, 2=JPY")
    status: bool = Field(..., description="True=Active, False=Inactive")
    created_at: datetime

    @validator("features", pre=True)
    def parse_features(cls, v):
        """Parse features from JSON string if needed"""
        if v is None:
            return {}
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {}
        elif isinstance(v, dict):
            return v
        else:
            return {}

    @validator("billing_cycle", pre=True)
    def parse_billing_cycle(cls, v):
        """Ensure billing_cycle is an integer"""
        try:
            return int(v) if v is not None else 1
        except (ValueError, TypeError):
            return 1

    @validator("currency", pre=True)
    def parse_currency(cls, v):
        """Ensure currency is an integer"""
        try:
            return int(v) if v is not None else 1
        except (ValueError, TypeError):
            return 1

    @validator("status", pre=True)
    def parse_status(cls, v):
        """Ensure status is a boolean"""
        if v is None:
            return True
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes", "on")
        try:
            return bool(int(v)) if isinstance(v, (int, float)) else bool(v)
        except (ValueError, TypeError):
            return True

    @validator("price", pre=True)
    def parse_price(cls, v):
        """Ensure price is an integer"""
        try:
            return int(v) if v is not None else 0
        except (ValueError, TypeError):
            return 0

    class Config:
        from_attributes = True
        json_encoders = {datetime: lambda v: v.isoformat()}


class UserSubscriptionResponse(BaseModel):
    id: int
    plan_id: int
    user_id: int
    expiration: datetime
    is_expired: bool
    scheduled_downgrade: bool
    plan_name: str
    plan_price: int
    status: int = Field(..., description="1=Active, 2=Cancelled, 3=Expired, 4=CANCELED")
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserSubscriptionSummary(BaseModel):
    user_id: int
    user_name: str
    user_email: str
    current_plan: Optional[str] = None
    plan_price: Optional[int] = None
    expiration: Optional[datetime] = None
    is_active: bool = False
    has_stripe_customer: bool = False
    stripe_customer_id: Optional[str] = None


class CreateCheckoutSessionRequest(BaseModel):
    plan_id: int


class CreateCheckoutSessionResponse(BaseModel):
    checkout_url: str
    session_id: str


class PaymentMethodResponse(BaseModel):
    id: str
    last4: str
    brand: str  # visa, mastercard, amex, etc.
    exp_month: int
    exp_year: int
    is_default: bool

    class Config:
        from_attributes = True
        json_encoders = {
            # Add any custom encoders if needed
        }

    @property
    def card_logo_url(self) -> str:
        """
        Return a URL or identifier for the card brand logo
        You can customize this based on where you store your card logos
        """
        brand_logos = {
            "visa": "/static/images/cards/visa.png",
            "mastercard": "/static/images/cards/mastercard.png",
            "amex": "/static/images/cards/amex.png",
            "discover": "/static/images/cards/discover.png",
            "jcb": "/static/images/cards/jcb.png",
            "diners": "/static/images/cards/diners.png",
            "unionpay": "/static/images/cards/unionpay.png",
        }
        return brand_logos.get(self.brand.lower(), "/static/images/cards/default.png")

    @property
    def display_name(self) -> str:
        """
        Return a user-friendly display name for the payment method
        """
        brand_name = self.brand.title()
        return f"{brand_name} ending in {self.last4}"


class InvoiceResponse(BaseModel):
    id: str
    date: str  # ISO format date string
    total: str  # Formatted total amount (e.g., "$20.00")
    status: str  # Invoice status (Paid, Open, Draft, etc.)
    invoice_url: str  # URL to view/download the invoice
    amount_paid: int  # Amount paid in cents
    amount_due: int  # Amount due in cents
    currency: str  # Currency code (USD, JPY, etc.)
    description: Optional[str] = None  # Invoice description
    period_start: Optional[str] = None  # Billing period start (ISO format)
    period_end: Optional[str] = None  # Billing period end (ISO format)

    class Config:
        from_attributes = True
        json_encoders = {datetime: lambda v: v.isoformat()}


# You might also want a simpler version for basic display
class InvoiceBasicResponse(BaseModel):
    id: str
    date: str
    total: str
    status: str
    invoice_url: str

    class Config:
        from_attributes = True


class CreateSetupIntentRequest(BaseModel):
    pass  # No additional fields needed, user_id comes from auth


class CreateSetupIntentResponse(BaseModel):
    success: bool
    client_secret: str
    setup_intent_id: str
    message: Optional[str] = None


class UpdatePaymentMethodResponse(BaseModel):
    success: bool
    message: str
    payment_method_id: Optional[str] = None


class UpdateSubscriptionRequest(BaseModel):
    """Request model for updating user subscription"""

    new_plan_id: int = Field(..., description="ID of the new subscription plan")
    proration_behavior: Optional[str] = Field(
        default="create_prorations",
        description=(
            "How to handle prorations: 'create_prorations', 'none', " "'always_invoice'"
        ),
    )


class UpdateSubscriptionResponse(BaseModel):
    """Response model for subscription updates"""

    success: bool
    message: str
    old_plan_name: str
    new_plan_name: str
    change_type: str  # "upgrade" or "downgrade"
    effective_date: Optional[int] = None
    next_billing_date: Optional[int] = None
    prorated_amount: Optional[str] = None


class CancelSubscriptionResponse(BaseModel):
    success: bool
    message: str
    cancellation_date: str
    access_until: str
