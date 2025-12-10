"""
Centralized constants for subscription management.

This module contains all constants, enums, and configuration values
used throughout the subscription system to ensure consistency and
ease of maintenance.
"""

from enum import IntEnum, StrEnum


# ============================================================================
# Subscription User Status
# ============================================================================
class SubscriptionUserStatus(IntEnum):
    """User's subscription status"""

    FREE = 1
    SUBSCRIBED = 2
    EXPIRED = 3
    CANCELED = 4


# ============================================================================
# Subscription Plan Types
# ============================================================================
class SubscriptionPlanType(IntEnum):
    """Subscription billing plan types"""

    MONTHLY = 1
    YEARLY = 2


# ============================================================================
# Subscription Status
# ============================================================================
class SubscriptionStatusType(IntEnum):
    """Subscription active/inactive status"""

    INACTIVE = 0
    ACTIVE = 1


# ============================================================================
# Currency Types
# ============================================================================
class SubscriptionCurrencyType(IntEnum):
    """Supported currency types"""

    USD = 1
    JPY = 2

    def get_currency_string(self) -> str:
        """Get the string representation of the currency"""
        if self == self.__class__.USD:
            return "usd"
        elif self == self.__class__.JPY:
            return "jpy"
        return None

    @staticmethod
    def get_currency_enum(value: str):
        """Get the enum representation of the currency"""
        value = value.lower()
        if value == "usd":
            return SubscriptionCurrencyType.USD
        elif value == "jpy":
            return SubscriptionCurrencyType.JPY
        return None


# ============================================================================
# Sync Status
# ============================================================================
class SyncStatus(StrEnum):
    """Synchronization status for subscriptions"""

    PENDING = "pending"
    SYNCED = "synced"
    FAILED = "failed"


# ============================================================================
# Cancellation Reasons
# ============================================================================
class CancellationReason(StrEnum):
    """Reasons for subscription cancellation"""

    USER_REQUEST = "user_request"
    PAYMENT_FAILED = "payment_failed"
    ADMIN_ACTION = "admin_action"
    REFUND = "refund"


# ============================================================================
# Stripe Subscription Status
# ============================================================================
class StripeSubscriptionStatus(StrEnum):
    """Stripe subscription status values"""

    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    TRIAL = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    UNPAID = "unpaid"
    PAUSED = "paused"


# ============================================================================
# Stripe Webhook Events
# ============================================================================
class StripeWebhookEvent(StrEnum):
    """Stripe webhook event types"""

    CHECKOUT_SESSION_COMPLETED = "checkout.session.completed"
    INVOICE_PAYMENT_FAILED = "invoice.payment_failed"
    CUSTOMER_SUBSCRIPTION_DELETED = "customer.subscription.deleted"
    SUBSCRIPTION_SCHEDULE_RELEASED = "subscription_schedule.released"
    INVOICE_PAYMENT_SUCCEEDED = "invoice.payment_succeeded"


# ============================================================================
# Stripe Checkout Status
# ============================================================================
class StripeCheckoutSessionStatus(StrEnum):
    """Stripe checkout session status values"""

    COMPLETE = "complete"
    EXPIRED = "expired"
    OPEN = "open"


class StripeCheckoutPaymentStatus(StrEnum):
    """Stripe checkout payment status values"""

    PAID = "paid"
    UNPAID = "unpaid"
    NO_PAYMENT_REQUIRED = "no_payment_required"


# ============================================================================
# Billing Cycles
# ============================================================================
class BillingCycle(StrEnum):
    """Billing cycle identifiers"""

    MONTHLY = "1"
    YEARLY = "2"


# ============================================================================
# Payment Status
# ============================================================================
class PaymentStatus(StrEnum):
    """Payment status values"""

    PAID = "paid"


# ============================================================================
# Active Status (for backward compatibility with Enum)
# ============================================================================
class SubscriptionActiveStatus(IntEnum):
    """Subscription active status (legacy enum format)"""

    ACTIVE = 1
    INACTIVE = 0


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
    PREMIUM = 200  # 200 GB for premium plan
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


# ============================================================================
# Configuration Constants
# ============================================================================

# Trial subscription configuration
TRIAL_PERIOD_DAYS = 30  # Number of days for trial subscription period

# Provider names
STRIPE_PROVIDER_NAME = "stripe"

# Timeouts and limits
DUPLICATE_PURCHASE_WINDOW_MINUTES = 30  # Window for detecting duplicate purchases
RECENT_SUBSCRIPTION_WINDOW_DAYS = 7  # Window for finding recently expired subscriptions
INVOICE_LIST_LIMIT = 100  # Maximum number of invoices to retrieve

# Payment method types
PAYMENT_METHOD_TYPE_CARD = "card"
PAYMENT_METHOD_TYPE_LINK = "link"

# Setup intent usage
SETUP_INTENT_USAGE_OFF_SESSION = "off_session"
