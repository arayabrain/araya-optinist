from typing import Any, Dict

from pydantic import BaseModel


class CheckoutSessionRequest(BaseModel):
    session_id: str


class WebhookRequest(BaseModel):
    event_type: str
    data: Dict[str, Any]
