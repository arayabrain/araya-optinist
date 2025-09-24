from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from sqlalchemy import BIGINT, JSON, TIMESTAMP, Boolean, DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.sql.functions import current_timestamp
from sqlmodel import Column, Field, SQLModel


# Enums for subscription management
class SyncStatus(str, Enum):
    PENDING = "pending"
    SYNCED = "synced"
    FAILED = "failed"


class CancellationReason(str, Enum):
    USER_REQUEST = "user_request"
    PAYMENT_FAILED = "payment_failed"
    ADMIN_ACTION = "admin_action"
    REFUND = "refund"


class SubscriptionPlans(SQLModel, table=True):
    __tablename__ = "subscription_plans"
    __table_args__ = (UniqueConstraint("id", name="idx_id"),)

    id: Optional[int] = Field(
        sa_column=Column(BIGINT, primary_key=True, nullable=False, autoincrement=True),
        default=None,
    )
    name: str = Field(sa_column=Column(String(100), nullable=False))
    price: int = Field(
        sa_column=Column(BIGINT, nullable=False)
    )  # Changed from float to int (cents)
    billing_cycle: int = Field(
        sa_column=Column(BIGINT, nullable=False),
        description="Billing cycle in enum format (e.g., 1 for monthly, 2 for yearly)",
    )
    # Fixed: Use JSON column type and make it properly typed
    features: Optional[Dict[str, Any]] = Field(
        sa_column=Column(JSON, nullable=False),
        description="JSON object of features included in the plan",
    )
    status: bool = Field(
        sa_column=Column(Boolean, nullable=False, default=True),
        description="True=Active, False=Inactive",
    )
    currency: int = Field(
        sa_column=Column(BIGINT, nullable=False, default=1),  # Fixed: Use BIGINT
        description="Currency code in enum format (e.g., 1 for USD, 2 for JPY)",
    )
    created_at: Optional[datetime] = Field(
        sa_column_kwargs={"server_default": current_timestamp()},
    )

    @property
    def formatted_price(self) -> str:
        return f"${self.price/100:.2f}" if self.price else "Free"


class UserSubscription(SQLModel, table=True):
    __tablename__ = "subscription_users"
    __table_args__ = (UniqueConstraint("id", name="idx_id"),)

    id: Optional[int] = Field(
        sa_column=Column(BIGINT, primary_key=True, nullable=False, autoincrement=True),
        default=None,
    )
    plan_id: int = Field(sa_column=Column(BIGINT, nullable=False))
    user_id: int = Field(sa_column=Column(BIGINT, nullable=False))
    expiration: datetime = Field(sa_column=Column(DateTime, nullable=False))
    scheduled_downgrade: bool = Field(
        sa_column=Column(Boolean, nullable=False, default=False),
        description="True if a downgrade is scheduled after the current period",
        default=False,
    )
    # Memo: When I used SyncStatus Enum directly, I got an error of value mismatch
    sync_status: SyncStatus = Field(
        sa_column=Column(
            SQLEnum("pending", "synced", "failed", name="sync_status_enum"),
            nullable=False,
            default="pending",
        ),
        default=SyncStatus.PENDING,
    )
    last_synced: Optional[datetime] = Field(
        sa_column_kwargs={"server_default": current_timestamp()},
    )
    created_at: Optional[datetime] = Field(
        sa_column_kwargs={"server_default": current_timestamp()},
    )
    updated_at: Optional[datetime] = Field(
        sa_column=Column(
            TIMESTAMP,
            server_default=func.current_timestamp(),
            onupdate=func.current_timestamp(),
        ),
    )


class SubscriptionProvider(SQLModel, table=True):
    __tablename__ = "subscription_providers"

    id: Optional[int] = Field(
        sa_column=Column(BIGINT, primary_key=True, nullable=False, autoincrement=True),
        default=None,
    )
    name: str = Field(
        sa_column=Column(String(50), nullable=False),
        description="Provider name (e.g., 'stripe', 'paypal')",
    )
    created_at: Optional[datetime] = Field(
        sa_column_kwargs={"server_default": current_timestamp()},
    )
    updated_at: Optional[datetime] = Field(
        sa_column=Column(
            TIMESTAMP,
            server_default=func.current_timestamp(),
            onupdate=func.current_timestamp(),
        ),
    )


class SubscriptionUserAccount(SQLModel, table=True):
    __tablename__ = "subscription_user_accounts"

    id: Optional[int] = Field(
        sa_column=Column(BIGINT, primary_key=True, nullable=False, autoincrement=True),
        default=None,
    )
    user_id: int = Field(sa_column=Column(BIGINT, nullable=False))
    provider_id: int = Field(
        sa_column=Column(BIGINT, nullable=False),
        description="FK to subscription_providers.id",
    )
    provider_customer_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="Provider's customer ID (e.g., Stripe customer ID)",
    )
    created_at: Optional[datetime] = Field(
        sa_column_kwargs={"server_default": current_timestamp()},
    )
    updated_at: Optional[datetime] = Field(
        sa_column=Column(
            TIMESTAMP,
            server_default=func.current_timestamp(),
            onupdate=func.current_timestamp(),
        ),
    )


class SubscriptionUserPurchase(SQLModel, table=True):
    __tablename__ = "subscription_user_purchases"

    id: Optional[int] = Field(
        sa_column=Column(BIGINT, primary_key=True, nullable=False, autoincrement=True),
        default=None,
    )
    plan_id: int = Field(
        sa_column=Column(BIGINT, nullable=False), description="1=FREE, 2=Premium"
    )
    user_id: int = Field(sa_column=Column(BIGINT, nullable=False))
    created_at: Optional[datetime] = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(TIMESTAMP, server_default=func.current_timestamp()),
    )
    updated_at: Optional[datetime] = Field(
        sa_column=Column(
            TIMESTAMP,
            server_default=func.current_timestamp(),
            onupdate=func.current_timestamp(),
        ),
    )


class SubscriptionCancellation(SQLModel, table=True):
    __tablename__ = "subscription_cancellations"

    id: Optional[int] = Field(
        sa_column=Column(BIGINT, primary_key=True, nullable=False, autoincrement=True),
        default=None,
    )
    cancelled_by_user_id: int = Field(sa_column=Column(BIGINT, nullable=False))
    purchases_id: int = Field(
        sa_column=Column(BIGINT, nullable=False),
        description="FK to subscription_user_purchases.id",
    )
    cancelled_at: Optional[datetime] = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(TIMESTAMP, server_default=func.current_timestamp()),
    )
    reason: Optional[CancellationReason] = Field(
        sa_column=Column(SQLEnum(CancellationReason), nullable=True),
        default=None,
        description="Reason for cancellation",
    )
    notes: Optional[str] = Field(
        sa_column=Column(Text, nullable=True),
        default=None,
        description="Additional notes or comments",
    )
