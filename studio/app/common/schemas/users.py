from datetime import datetime
from enum import Enum
from typing import Optional

from fastapi import Query
from pydantic import BaseModel, EmailStr, Field

from studio.app.common.models.subscription import (
    PlanName,
    SubscriptionStatus,
    SubscriptionType,
)

password_regex = r"^(?=.*\d)(?=.*[!#$%&()*+,-./@_|])(?=.*[a-zA-Z]).{6,255}$"


class Organization(BaseModel):
    id: int
    name: str

    class Config:
        orm_mode = True


class UserSearchOptions(BaseModel):
    email: Optional[str] = Field(Query(default=""))
    name: Optional[str] = Field(Query(default=""))


class UserRole(int, Enum):
    admin = 1
    operator = 20


class User(BaseModel):
    id: int
    uid: str
    name: Optional[str]
    email: EmailStr
    organization: Organization
    role_id: Optional[int]
    data_usage: Optional[int]
    attributes: Optional[dict]
    subscription_plan_name: Optional[str] = None
    subscription_status: Optional[str] = None
    subscription_days_remaining: Optional[int] = None
    storage_usage_bytes: Optional[int] = None
    storage_quota_bytes: Optional[int] = None
    storage_usage_percent: Optional[float] = None

    @property
    def is_admin(self) -> bool:
        return self.role_id == UserRole.admin

    @property
    def has_active_subscription(self) -> bool:
        """Check if user has an active paid subscription."""
        return (
            self.subscription_plan_name == PlanName.PREMIUM.value
            and self.subscription_status
            in [SubscriptionStatus.PREMIUM.value, SubscriptionStatus.LIMIT_GRACE.value]
        )

    @property
    def subscription_type(self) -> str:
        """Get current effective Subscription Type: 'premium' or 'free'."""
        return (
            SubscriptionType.PREMIUM.value
            if self.has_active_subscription
            else SubscriptionType.FREE.value
        )

    @property
    def remote_bucket_name(self) -> str:
        return self.attributes.get("remote_bucket_name") if self.attributes else None

    class Config:
        orm_mode = True


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(max_length=255, regex=password_regex)
    name: str
    role_id: int

    class Config:
        anystr_strip_whitespace = True


class UserUpdate(BaseModel):
    email: Optional[EmailStr]
    name: str
    role_id: int


class SelfUserUpdate(BaseModel):
    email: Optional[EmailStr]
    name: str


class UserPasswordUpdate(BaseModel):
    old_password: str
    new_password: str = Field(max_length=255, regex=password_regex)

    class Config:
        anystr_strip_whitespace = True


class UserInfo(BaseModel):
    id: int
    name: Optional[str]
    email: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        orm_mode = True
