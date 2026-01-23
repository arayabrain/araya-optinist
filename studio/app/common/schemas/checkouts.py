from enum import StrEnum
from typing import Any, Dict, Optional

from pydantic import BaseModel


class CheckoutValidationStatus(StrEnum):
    SUCCESS = "success"
    PAYMENT_FAILED = "payment_failed"
    WEBHOOK_FAILED = "webhook_failed"


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

    status: CheckoutValidationStatus
    message: Optional[str] = None
