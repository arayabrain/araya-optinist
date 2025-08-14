from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import JSON, Boolean, DateTime, String, text
from sqlalchemy.dialects.mysql import BIGINT, TINYINT
from sqlalchemy.sql import func
from sqlmodel import Column, Field

from studio.app.common.models.base import Base, TimestampMixin


class SubscriptionPlans(Base, table=True):
    __tablename__ = "subscription_plans"

    name: str = Field(sa_column=Column(String(100), nullable=False))
    price: int = Field(
        sa_column=Column(BIGINT(unsigned=True), nullable=False)
    )  # Changed from float to int (cents)
    billing_cycle: int = Field(
        sa_column=Column(BIGINT(unsigned=True), nullable=False),
        description="Billing cycle in enum format (e.g., 1 for monthly, 2 for yearly)",
    )
    # Fixed: Features are required (not Optional) to match database schema
    features: Dict[str, Any] = Field(
        sa_column=Column(JSON, nullable=False),
        description="JSON object of features included in the plan",
    )
    status: bool = Field(
        sa_column=Column(Boolean, nullable=False, server_default=text("1")),
        description="True=Active, False=Inactive",
    )
    currency: int = Field(
        sa_column=Column(TINYINT(unsigned=True), nullable=False, default=1),
        description="Currency code in enum format (e.g., 1 for USD, 2 for JPY)",
    )
    created_at: Optional[datetime] = Field(
        sa_column=Column(
            DateTime, nullable=False, server_default=func.current_timestamp()
        ),
    )

    @property
    def formatted_price(self) -> str:
        return f"${self.price/100:.2f}" if self.price else "Free"


class UserSubscription(Base, TimestampMixin, table=True):
    __tablename__ = "subscription_users"

    plan_id: int = Field(sa_column=Column(BIGINT(unsigned=True), nullable=False))
    user_id: int = Field(sa_column=Column(BIGINT(unsigned=True), nullable=False))
    expiration: datetime = Field(sa_column=Column(DateTime, nullable=False))


class UserStorageUsage(Base, table=True):
    __tablename__ = "user_storage_usage"

    user_id: int = Field(
        sa_column=Column(BIGINT(unsigned=True), nullable=False, unique=True)
    )
    current_usage_bytes: int = Field(
        sa_column=Column(
            BIGINT(unsigned=True), nullable=False, server_default=text("0")
        )
    )
    quota_limit_bytes: int = Field(
        sa_column=Column(BIGINT(unsigned=True), nullable=False)
    )
    last_updated: Optional[datetime] = Field(
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
    )
    created_at: Optional[datetime] = Field(
        sa_column=Column(
            DateTime, nullable=False, server_default=func.current_timestamp()
        ),
    )

    @property
    def usage_percentage(self) -> float:
        """Calculate usage percentage."""
        if self.quota_limit_bytes == 0:
            return 0.0
        return round((self.current_usage_bytes / self.quota_limit_bytes) * 100, 2)
