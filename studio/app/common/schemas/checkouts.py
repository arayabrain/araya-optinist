from typing import Any, Dict, Optional

from pydantic import BaseModel


class CheckoutSessionRequest(BaseModel):
    session_id: str


class WebhookRequest(BaseModel):
    event_type: str
    data: Dict[str, Any]


class CheckoutValidationResponse(BaseModel):
    """
    Response for checkout session validation

    status values:
    - "success": Payment succeeded and webhook updated database
    - "payment_failed": Payment itself failed
      (card declined, insufficient funds, etc.)
    - "webhook_failed": Payment succeeded but webhook didn't update
      database (internal error)
    """

    status: str  # "success", "payment_failed", or "webhook_failed"
    message: Optional[str] = None
