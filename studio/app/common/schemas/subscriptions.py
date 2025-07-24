from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class SubscriptionPlanResponse(BaseModel):
    id: int
    name: str
    price: int
    created_at: datetime

    class Config:
        from_attributes = True


class UserSubscriptionResponse(BaseModel):
    id: int
    plan_id: int
    user_id: int
    expiration: datetime
    plan_name: str
    plan_price: int
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
