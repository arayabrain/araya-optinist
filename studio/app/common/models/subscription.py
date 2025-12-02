from datetime import datetime
from enum import StrEnum
from typing import Any, Dict, Optional

from sqlalchemy import BIGINT, JSON, TIMESTAMP, Boolean, DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.sql.functions import current_timestamp
from sqlmodel import Column, Field, SQLModel


# Storage size constants (in bytes)
class StorageSize:
    """Constants for storage size calculations"""

    KB = 1024  # 1 Kilobyte
    MB = 1024 * 1024  # 1 Megabyte
    GB = 1024 * 1024 * 1024  # 1 Gigabyte
    TB = 1024 * 1024 * 1024 * 1024  # 1 Terabyte


class StorageQuota:
    """Constants for storage quota limits"""

    FREE = 5  # 5 GB for free plan
    PREMIUM = 100  # 100 GB for premium plan
    CRITICAL_THRESHOLD_PERCENT = 90  # 90% usage threshold for critical warning
    DANGER_THRESHOLD_PERCENT = 100  # 100% usage threshold for danger warning


class SubscriptionPeriods:
    """
    Constants for subscription period calculations
    Note: any updates should also be reflected in
    frontend/src/const/Subscription.ts SubscriptionPeriods
    """

    TRIAL_PERIOD_DAYS = 30
    GRACE_PERIOD_DAYS = 30
    WARNING_PERIOD_DAYS = 30
    STORAGE_WARNING_DAYS = 30  # Days to remove excess storage for free users

    # Cache age for storage usage (in minutes)
    MAX_CACHE_AGE_MINUTES = 20

    # Progress calculation constants
    MAX_PROGRESS_PERCENT = 100
    MIN_PROGRESS_PERCENT = 0
    PROGRESS_REFERENCE_DAYS = 30  # Reference period for progress bar (30 days)

    # Warning color thresholds (days remaining)
    CRITICAL_THRESHOLD_DAYS = 0  # Red/error
    URGENT_THRESHOLD_DAYS = 7  # Red/error
    WARNING_THRESHOLD_DAYS = 14  # Yellow/warning


class SubscriptionPlanIds:
    """
    Constants for subscription plan database IDs.

    Usage: Database queries comparing plan_id field
    Example: if subscription_plan_id == SubscriptionPlanIds.FREE

    Note: Different from SubscriptionType (string identifiers) and
    PlanName (display names)
    """

    FREE = 1
    PREMIUM = 2


# Enums for subscription management
class SyncStatus(StrEnum):
    PENDING = "pending"
    SYNCED = "synced"
    FAILED = "failed"


class SubscriptionType(StrEnum):
    """
    String identifiers for subscription types.

    Usage: Type discriminator in API responses and business logic
    Example: if subscription_type == SubscriptionType.FREE.value

    Note: Different from SubscriptionPlanIds (database IDs) and
    PlanName (display strings)
    """

    PREMIUM = "premium"
    FREE = "free"


class PlanName(StrEnum):
    """
    Display names for subscription plans.

    Usage: UI labels and user-facing text
    Example: user.__dict__["subscription_plan_name"] = PlanName.FREE.value

    Note: Different from SubscriptionPlanIds (database IDs) and
    SubscriptionType (type identifiers)
    """

    PREMIUM = "Premium"
    FREE = "Free"
    UNKNOWN = "Unknown"  # Fallback for when plan cannot be determined


class SubscriptionStatus(StrEnum):
    """
    User subscription status labels.

    Usage: Represents current state of user's subscription for display
    Example: user.__dict__["subscription_status"] = SubscriptionStatus.FREE.value
    """

    FREE = "Free"  # User on free plan
    PREMIUM = "Premium"  # Active premium subscription
    LIMIT_GRACE = "Limit Grace"  # Premium expired, in grace period
    EXPIRED = "Expired"  # Grace period ended


class SubscriptionLifecycleStatus(StrEnum):
    """
    Lifecycle status for subscription expiration checking in limit warnings.

    Usage: Determines warning state based on subscription expiration timeline
    Example: Used in calculate_limit_warning() to decide warning type
    """

    ACTIVE = "active"  # Subscription has not expired yet
    GRACE = "grace"  # In grace period after expiration
    WARNING = "warning"  # In warning period (after grace, before deletion)
    OVERDUE = "overdue"  # Past warning period
    FREE = "free"  # Never had premium subscription


class CancellationReason(StrEnum):
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
    stripe_product_id: Optional[str] = Field(
        sa_column=Column(String(255), nullable=True),
        default=None,
        description="Stripe product ID for this subscription plan",
    )
    stripe_price_id: Optional[str] = Field(
        sa_column=Column(String(255), nullable=True),
        default=None,
        description="Stripe price ID for this subscription plan",
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
        sa_column=Column(
            SQLEnum(
                CancellationReason,
                name="cancellation_reason_enum",
                create_type=False,
                values_callable=lambda x: [e.value for e in x],
            ),
            nullable=True,
        ),
        default=None,
        description="Reason for cancellation",
    )
    notes: Optional[str] = Field(
        sa_column=Column(Text, nullable=True),
        default=None,
        description="Additional notes or comments",
    )


class UserStorageUsage(SQLModel, table=True):
    __tablename__ = "user_storage_usage"
    __table_args__ = (UniqueConstraint("id", name="idx_id"),)

    id: Optional[int] = Field(
        sa_column=Column(BIGINT, primary_key=True, nullable=False, autoincrement=True),
        default=None,
    )
    user_id: int = Field(sa_column=Column(BIGINT, nullable=False, unique=True))
    storage_usage_bytes: int = Field(
        sa_column=Column(BIGINT, nullable=False, default=0)
    )
    storage_quota_bytes: int = Field(sa_column=Column(BIGINT, nullable=False))
    last_updated: Optional[datetime] = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(
            DateTime, nullable=False, server_default=func.current_timestamp()
        ),
    )
    created_at: Optional[datetime] = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(
            DateTime, nullable=False, server_default=func.current_timestamp()
        ),
    )

    @property
    def storage_usage_percent(self) -> float:
        """Calculate usage percentage."""
        if self.storage_quota_bytes == 0:
            return 0.0
        return round((self.storage_usage_bytes / self.storage_quota_bytes) * 100, 2)
