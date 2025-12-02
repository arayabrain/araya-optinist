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
class SubscriptionActiveStatus(StrEnum):
    """Subscription active status (legacy enum format)"""

    ACTIVE = "1"
    INACTIVE = "0"


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
