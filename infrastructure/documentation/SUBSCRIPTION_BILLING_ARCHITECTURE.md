# Subscription and Billing System Architecture

## Executive Summary

This document describes the subscription and billing system integrated with Stripe for handling user payments, plan management, and subscription lifecycle events. The system uses Stripe as the payment provider with webhooks to handle real-time billing events.

**Key Components:**
- **StripeService** - Direct Stripe API integration for payment methods and subscriptions
- **SubscriptionService** - Business logic for subscription state and database operations
- **WebhookService** - Event-driven handlers for Stripe webhook notifications

**Supported Features:**
- Payment method management (cards via Stripe Elements)
- Subscription creation, updates, and cancellation
- Prorated plan changes (upgrade/downgrade)
- Grace period handling for storage limits
- Trial periods and scheduled plan changes

---

## Architecture Overview

```
                                    ┌─────────────────────────┐
                                    │       Frontend          │
                                    │  (React Components)     │
                                    └───────────┬─────────────┘
                                                │
                                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           FastAPI Backend                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌──────────────────┐ │
│  │   StripeService     │  │ SubscriptionService │  │  WebhookService  │ │
│  │                     │  │                     │  │                  │ │
│  │ - Payment methods   │  │ - Plan queries      │  │ - Checkout       │ │
│  │ - Setup intents     │  │ - Status tracking   │  │ - Invoices       │ │
│  │ - Subscriptions     │  │ - Database ops      │  │ - Cancellations  │ │
│  │ - API calls         │  │ - Grace periods     │  │ - Plan changes   │ │
│  └──────────┬──────────┘  └──────────┬──────────┘  └────────┬─────────┘ │
│             │                        │                      │           │
└─────────────┼────────────────────────┼──────────────────────┼───────────┘
              │                        │                      │
              ▼                        ▼                      ▼
       ┌──────────────┐         ┌──────────────┐       ┌──────────────┐
       │   Stripe     │         │   MySQL      │       │   Stripe     │
       │   API        │         │   Database   │       │   Webhooks   │
       └──────────────┘         └──────────────┘       └──────────────┘
```

---

## Service Components

### 1. StripeService

**File:** `studio/app/common/core/subscription/stripe_service.py`

The StripeService handles direct interactions with the Stripe API for payment operations.

#### Key Methods

| Method | Purpose |
|--------|---------|
| `get_default_payment_method()` | Get user's default payment method with card details |
| `create_setup_intent()` | Create a SetupIntent for adding payment methods |
| `update_default_payment_method()` | Set a new default payment method for subscription |
| `delete_payment_method()` | Remove a payment method from user's account |
| `handle_get_user_payment_methods()` | List all payment methods for a user |
| `handle_update_user_subscription()` | Update subscription plan with proration |
| `handle_cancel_user_subscription()` | Cancel subscription at period end |

#### Payment Method Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. User clicks "Add Payment Method"                                      │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 2. Frontend calls: POST /subscription/payments/setup-intent             │
│    → Backend creates Stripe SetupIntent                                 │
│    → Returns client_secret for Stripe Elements                          │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 3. User enters card details in Stripe Elements (secure iframe)          │
│    → Card data never touches our servers                                │
│    → Stripe validates and tokenizes card                                │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 4. Frontend calls: POST /subscription/payments/default                  │
│    → Backend attaches PaymentMethod to Customer                         │
│    → Sets as default for subscription                                   │
└─────────────────────────────────────────────────────────────────────────┘
```

#### Subscription Update Flow

```python
def handle_update_user_subscription(user_id: int, new_price_id: str):
    """
    Update user's subscription to a new plan.

    Handles:
    - Plan upgrades (prorated credit)
    - Plan downgrades (scheduled at period end)
    - Same plan (no-op)
    """
    # Get current subscription
    subscription = get_user_subscription(user_id)

    # Determine change type
    if is_upgrade(current_plan, new_plan):
        # Immediate upgrade with proration
        stripe.Subscription.modify(
            subscription_id,
            items=[{"id": item_id, "price": new_price_id}],
            proration_behavior="create_prorations",
        )
    else:
        # Schedule downgrade at billing period end
        stripe.SubscriptionSchedule.create(
            from_subscription=subscription_id,
            phases=[
                {"items": [{"price": current_price_id}], "end_date": "period_end"},
                {"items": [{"price": new_price_id}]},
            ],
        )
```

---

### 2. SubscriptionService

**File:** `studio/app/common/core/subscription/subscription_service.py`

The SubscriptionService handles business logic and database operations for subscriptions.

#### Key Methods

| Method | Purpose |
|--------|---------|
| `get_active_plans()` | Get all available subscription plans |
| `get_user_subscription()` | Get user's current subscription details |
| `get_subscription_status()` | Check subscription status and cancellation state |
| `is_subscription_cancelled()` | Check if subscription is scheduled for cancellation |
| `update_scheduled_downgrade()` | Update subscription with scheduled plan change |

#### Subscription States

| Status | Description | Access Level |
|--------|-------------|--------------|
| `ACTIVE` | Normal subscription, payment current | Full plan features |
| `PREMIUM` | Premium tier subscription active | Premium compute |
| `TRIALING` | Trial period active | Full plan features |
| `PAST_DUE` | Payment failed, retry in progress | Full access (temporary) |
| `LIMIT_GRACE` | Premium expired, 30-day grace period | Full premium access (see below) |
| `CANCELLED` | Scheduled for cancellation | Full until period end |
| `UNPAID` | Payment failed, all retries exhausted | Restricted access |

**LIMIT_GRACE details:** When a premium subscription expires, the user
enters a 30-day grace period. During this period:

- **Access is functionally identical to Premium** — the user retains
  premium compute routing, 200 GB storage quota, and
  `has_active_subscription` returns `True`
  (see `users.py:has_active_subscription`, `RoutingService.ts`)
- **Workflow execution** is allowed as long as storage stays under quota
  (the same quota check that applies to all statuses)
- **Alerts** warn the user that their subscription has expired and
  show a countdown of grace days remaining
- After the 30-day grace period, the status transitions to `EXPIRED`
  and access drops to Free tier (5 GB quota, no premium compute)

#### Grace Period Logic

```python
def calculate_limit_warning(user_id: int) -> dict:
    """
    Check if user has exceeded free plan limits after subscription ends.

    Returns alert info including:
    - alert_type: "storage" or "grace" or "overdue"
    - days_remaining: Days until data deletion
    - excess_data_bytes: Amount over limit
    - deletion_date: When data will be purged
    """
    storage_info = get_user_storage_usage(user_id)

    # Check if user has exceeded free tier limits
    if storage_usage > FREE_TIER_STORAGE_LIMIT:
        grace_end = subscription_end + timedelta(days=30)
        return {
            "has_alert": True,
            "alert_type": AlertType.STORAGE,
            "days_remaining": (grace_end - now).days,
            "excess_data_bytes": storage_usage - FREE_TIER_STORAGE_LIMIT,
            "deletion_date": grace_end.isoformat(),
        }
```

---

### 3. WebhookService

**File:** `studio/app/common/core/subscription/webhook_service.py`

The WebhookService processes Stripe webhook events for real-time subscription updates.

#### Handled Webhook Events

| Event | Handler | Action |
|-------|---------|--------|
| `checkout.session.completed` | `handle_checkout_completed()` | Create subscription, update user |
| `invoice.payment_succeeded` | `handle_subscription_payment_succeeded()` | Record payment, update status |
| `invoice.payment_failed` | `handle_payment_failed()` | Mark past_due, notify user |
| `customer.subscription.deleted` | `handle_subscription_cancelled()` | Clean up subscription records |
| `subscription_schedule.released` | `handle_subscription_schedule_released()` | Apply scheduled plan change |
| `invoice.created` | `handle_invoice_created()` | Pre-invoice processing |
| `invoice.finalized` | `handle_invoice_finalized()` | Post-invoice finalization |

#### Webhook Processing Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Stripe Webhook Delivery                                                  │
│ POST /webhooks/stripe                                                    │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. Verify webhook signature                                              │
│    → Prevents spoofed events                                             │
│    → Uses STRIPE_WEBHOOK_SECRET                                          │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 2. Parse event type and data                                             │
│    → Extract subscription/customer IDs                                   │
│    → Map to internal user                                                │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 3. Dispatch to appropriate handler                                       │
│    → dispatch_webhook_event() routes by event type                       │
│    → Each handler updates database accordingly                           │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 4. Return 200 OK to Stripe                                               │
│    → Prevents retry delivery                                             │
│    → Failures are logged but don't reject webhook                        │
└─────────────────────────────────────────────────────────────────────────┘
```

#### Checkout Completion Handler

```python
def handle_checkout_completed(event_data: dict, db: Session):
    """
    Handle successful checkout session completion.

    Actions:
    1. Extract customer and subscription IDs
    2. Look up user by email or create new user
    3. Create/update subscription record
    4. Update user's plan and storage quota
    5. Clear any grace period warnings
    """
    session = event_data["object"]
    customer_id = session["customer"]
    subscription_id = session["subscription"]

    # Get subscription details from Stripe
    subscription = stripe.Subscription.retrieve(subscription_id)

    # Update database
    user = get_user_by_stripe_customer(customer_id, db)
    update_user_subscription(
        user_id=user.id,
        subscription_id=subscription_id,
        plan_id=subscription["items"]["data"][0]["price"]["id"],
        status="active",
        current_period_end=subscription["current_period_end"],
    )
```

#### Payment Failure Handler

```python
def handle_payment_failed(event_data: dict, db: Session):
    """
    Handle failed invoice payment.

    Actions:
    1. Mark subscription as past_due
    2. Send failure notification email
    3. Schedule retry attempts (Stripe automatic)
    4. After all retries fail, transition to unpaid
    """
    invoice = event_data["object"]
    customer_id = invoice["customer"]

    # Update subscription status
    user = get_user_by_stripe_customer(customer_id, db)
    update_subscription_status(user.id, "past_due")

    # Stripe will automatically retry based on settings
    # After exhausted retries: customer.subscription.deleted event
```

---

## Database Schema

### UserSubscription Model

| Field | Type | Description |
|-------|------|-------------|
| `user_id` | INT (FK) | Reference to user |
| `stripe_customer_id` | VARCHAR | Stripe Customer ID |
| `stripe_subscription_id` | VARCHAR | Stripe Subscription ID |
| `plan_id` | VARCHAR | Stripe Price ID |
| `status` | ENUM | Subscription status |
| `current_period_start` | DATETIME | Billing period start |
| `current_period_end` | DATETIME | Billing period end |
| `cancel_at_period_end` | BOOLEAN | Cancellation scheduled |
| `trial_end` | DATETIME | Trial expiration date |
| `scheduled_plan_id` | VARCHAR | Downgrade plan (scheduled) |

### UserStorageUsage Model

| Field | Type | Description |
|-------|------|-------------|
| `user_id` | INT (FK) | Reference to user |
| `storage_usage_bytes` | BIGINT | Current storage used |
| `storage_quota_bytes` | BIGINT | Plan storage limit |
| `delta_since_last_scan` | BIGINT | Change since reconciliation |
| `last_full_scan` | DATETIME | Last S3 scan timestamp |

---

## API Endpoints

### Subscription Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/subscription/plans` | GET | List available plans |
| `/subscription/me` | GET | Get current user's subscription |
| `/subscription/checkout` | POST | Create checkout session |
| `/subscription/portal` | POST | Create customer portal session |
| `/subscription/cancel` | POST | Cancel subscription |
| `/subscription/update` | POST | Change subscription plan |

### Payment Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/subscription/payments` | GET | List payment methods |
| `/subscription/payments/default` | GET | Get default payment method |
| `/subscription/payments/default` | POST | Set default payment method |
| `/subscription/payments/{id}` | DELETE | Remove payment method |
| `/subscription/payments/setup-intent` | POST | Create SetupIntent |

### Webhook Endpoint

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/webhooks/stripe` | POST | Receive Stripe webhooks |

---

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `STRIPE_API_KEY` | Stripe secret key | Yes |
| `STRIPE_WEBHOOK_SECRET` | Webhook signature secret | Yes |
| `STRIPE_PUBLISHABLE_KEY` | Stripe public key (frontend) | Yes |
| `STRIPE_PREMIUM_PRICE_ID` | Price ID for premium plan | Yes |
| `STRIPE_FREE_PRICE_ID` | Price ID for free plan | Yes |

### Stripe Configuration

```python
# Plan configuration (Terraform or Stripe Dashboard)
plans = {
    "free": {
        "price_id": "price_free_monthly",
        "storage_gb": 5,
        "features": ["Basic workflow execution", "Community support"],
    },
    "premium": {
        "price_id": "price_premium_monthly",
        "storage_gb": 100,
        "features": ["Dedicated compute", "Priority support", "Advanced analytics"],
    },
}
```

---

## Premium Lifecycle: Free to Premium to Cancellation to Data Deletion

This section describes the complete user lifecycle from free signup through
premium subscription, cancellation, grace periods, and eventual data cleanup.

### End-to-End Lifecycle Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        SUBSCRIPTION LIFECYCLE                                   │
└─────────────────────────────────────────────────────────────────────────────────┘

  ┌──────────┐     Checkout      ┌───────────┐
  │          │    completed      │           │
  │   FREE   │ ────────────────> │  PREMIUM  │
  │  (5 GB)  │                   │ (200 GB)  │
  │          │ <──────────────── │           │
  └──────────┘   Subscription    └─────┬─────┘
       ^         cancelled             │
       │                               │ Subscription expires
       │                               │ (cancel_at_period_end or payment failure)
       │                               ▼
       │                         ┌───────────┐
       │   Storage <= 5 GB       │   GRACE   │  Days 0-30 after expiration
       │   (no warning needed)   │  PERIOD   │  Quota drops to 5 GB (free limit)
       │ <────────────────────── │           │  Storage > 5 GB? → GRACE alert
       │                         └─────┬─────┘
       │                               │
       │                               │ Grace period ends (day 30)
       │                               ▼
       │                         ┌───────────┐
       │   Storage <= 5 GB       │  WARNING  │  Days 30-60 after expiration
       │   (no warning needed)   │  PERIOD   │  "Data will be deleted in X days"
       │ <────────────────────── │           │
       │                         └─────┬─────┘
       │                               │
       │                               │ Warning period ends (day 60)
       │                               ▼
       │                         ┌───────────┐
       │   Storage <= 5 GB       │  OVERDUE  │  Day 60+ after expiration
       │   (no warning needed)   │           │  "Data scheduled for deletion"
       │ <────────────────────── │           │
       │                         └───────────┘
       │
       │         User can re-subscribe at ANY point to return to PREMIUM
       └─────────────────────────────────────────────────────────────────
```

**Key principle:** Warnings only appear when the user's storage exceeds the
free tier limit (5 GB). Users whose data fits within 5 GB transition silently
back to free with no alerts, regardless of lifecycle stage.

### Timeline After Subscription Expiration

```
Subscription    Grace Period     Warning Period     Overdue
  Expires          Ends              Ends          (ongoing)
    │                │                 │               │
    T             T + 30d          T + 60d            ...
    │                │                 │               │
    ├────────────────┼─────────────────┼───────────────┤
    │  GRACE (30d)   │  WARNING (30d)  │   OVERDUE     │
    │                │                 │               │
    │  Alert: GRACE  │  Alert: GRACE   │ Alert: OVERDUE│
    │  "X days of    │  "Data deleted  │ "Scheduled    │
    │   premium      │   in X days"   │  for deletion"│
    │   remaining"   │                 │               │
    └────────────────┴─────────────────┴───────────────┘

  Key dates computed by calculate_limit_warning():
    subscription_end  = UserSubscription.expiration
    grace_end         = subscription_end + 30 days (GRACE_PERIOD_DAYS)
    deletion_date     = grace_end + 30 days (WARNING_PERIOD_DAYS)
```

### Subscription Lifecycle States

| Status | Duration | Storage Quota | Alert Type | User Experience |
|--------|----------|---------------|------------|-----------------|
| `FREE` | Indefinite | 5 GB | `storage` (if over) | Full free features |
| `ACTIVE` | Billing period | 200 GB | `storage` (if over) | Full premium features |
| `GRACE` | Days 0-30 | Measured against 5 GB | `grace` | Premium features, countdown warning |
| `WARNING` | Days 30-60 | Measured against 5 GB | `grace` | "Data will be deleted in X days" |
| `OVERDUE` | Day 60+ | Measured against 5 GB | `overdue` | "Data scheduled for deletion" |

### Limit Warning Decision Tree

`calculate_limit_warning()` evaluates 5 cases to determine the alert:

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     calculate_limit_warning(user_id)                      │
└──────────────────────────────────────────────────────────────────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │  Get subscription status    │
                    │  Get storage usage           │
                    └──────────────┬──────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
        ┌──────────┐        ┌──────────┐        ┌──────────────────┐
        │   FREE   │        │  ACTIVE  │        │ GRACE / WARNING  │
        │  (no     │        │ (premium │        │   / OVERDUE      │
        │  premium │        │  active) │        │ (premium expired)│
        │  history)│        │          │        │                  │
        └────┬─────┘        └────┬─────┘        └────────┬─────────┘
             │                   │                       │
        ┌────┴────┐         ┌────┴────┐            ┌─────┴─────┐
        │ > 5 GB? │         │> 200 GB?│            │  > 5 GB?  │
        └────┬────┘         └────┬────┘            └─────┬─────┘
          Y     N            Y     N                 Y       N
          │     │            │     │                 │       │
          ▼     ▼            ▼     ▼                 ▼       ▼
      Case 2  Case 1    Case 3  No alert        Cases 4/5  No alert
      STORAGE  None     STORAGE                  GRACE or
      alert             alert                    OVERDUE alert
```

| Case | Subscription | Storage | Alert Type | `days_remaining` |
|------|-------------|---------|------------|------------------|
| 1 | Free, never premium | Under 5 GB | None | -- |
| 2 | Free, never premium | Over 5 GB | `storage` | 30 (fixed) |
| 3 | Premium active | Over 200 GB | `storage` | 30 (fixed) |
| 4 | Grace/Warning period | Over 5 GB | `grace` | Countdown to grace_end or deletion_date |
| 5 | Overdue (60+ days) | Over 5 GB | `overdue` | 0 |

### LimitWarning Response Schema

**File:** `studio/app/common/schemas/storage.py`

```python
class LimitWarning(BaseModel):
    has_alert: bool               # Always True when returned
    alert_type: str               # "storage", "grace", or "overdue"
    days_remaining: int           # Days before action required (0 = overdue)
    excess_data_bytes: int        # Bytes over effective quota
    excess_data_gb: float         # GB over effective quota
    storage_usage_bytes: int      # Current total storage usage
    storage_usage_gb: float
    storage_quota_bytes: int      # Effective quota (5 GB if grace/overdue)
    storage_quota_gb: float
    message: str                  # Human-readable warning

    # Subscription-related (only set for grace/warning/overdue alerts)
    subscription_end_date: str    # ISO date when premium expired
    grace_end_date: str           # ISO date when grace period ends (T+30d)
    deletion_date: str            # ISO date when data deletion begins (T+60d)
```

### Warning Messages by Status

| Status | Storage Exceeded | Example Message |
|--------|-----------------|-----------------|
| `GRACE` | Yes | "Your premium subscription expired on Jan 15, 2025. Your storage (12.0 GB) exceeds the free plan limit (5 GB). You have 22 days to upgrade or remove 7.0 GB of data." |
| `WARNING` | Yes | "Your premium subscription expired on Jan 15, 2025. Your storage (12.0 GB) exceeds the free plan limit (5 GB). Remove 7.0 GB of data within 14 days or your data will be deleted." |
| `OVERDUE` | Yes | "Your premium subscription expired on Jan 15, 2025. Your storage (12.0 GB) exceeds the free plan limit (5 GB). Your data is scheduled for deletion. Please upgrade or remove 7.0 GB." |
| `FREE` | Yes | "Your data usage (7.0 GB) exceeds the free plan limit (5.0 GB). Please upgrade or remove 2.0 GB of data within 30 days." |
| `ACTIVE` | Yes | "Your storage usage (210.0 GB) is over the limit for your plan. You will be unable to run workflows. Consider cleaning up unused data." |

### Constants Reference

**File:** `studio/app/common/core/subscription/constants.py`

```python
# Storage quotas
StorageQuota.FREE = 5                            # 5 GB
StorageQuota.PREMIUM = 200                       # 200 GB

# Lifecycle periods
SubscriptionPeriods.GRACE_PERIOD_DAYS = 30       # Grace period after expiry
SubscriptionPeriods.WARNING_PERIOD_DAYS = 30     # Warning period after grace
SubscriptionPeriods.STORAGE_WARNING_DAYS = 30    # Days for free users to reduce
SubscriptionPeriods.QUOTA_DROP_WARNING_DAYS = 3  # Warn before quota drops

# Frontend severity thresholds (days remaining)
SubscriptionPeriods.CRITICAL_THRESHOLD_DAYS = 0  # Red/error
SubscriptionPeriods.URGENT_THRESHOLD_DAYS = 7    # Red/error
SubscriptionPeriods.WARNING_THRESHOLD_DAYS = 14  # Yellow/warning
```

### Enums Reference

**File:** `studio/app/common/core/subscription/constants.py`

```python
class SubscriptionLifecycleStatus(StrEnum):
    ACTIVE = "active"      # Premium not yet expired
    GRACE = "grace"        # Days 0-30 after expiration
    WARNING = "warning"    # Days 30-60 after expiration
    OVERDUE = "overdue"    # Day 60+ after expiration
    FREE = "free"          # Never had premium

class AlertType(StrEnum):
    STORAGE = "storage"    # Storage quota exceeded (any plan)
    GRACE = "grace"        # Grace or warning period (maps from GRACE + WARNING)
    OVERDUE = "overdue"    # Past all grace periods, deletion imminent

class SubscriptionStatus(StrEnum):
    FREE = "Free"
    PREMIUM = "Premium"
    LIMIT_GRACE = "Limit Grace"
    EXPIRED = "Expired"
```

### Limit Warning API Endpoints

**File:** `studio/app/common/routers/storage_limit_alerts.py`

| Endpoint | Method | Purpose | Response |
|----------|--------|---------|----------|
| `/storage-limit-alerts/me` | GET | Storage alert for current user | `{ has_alert, alert }` |
| `/storage-limit-alerts/usage` | GET | Detailed storage usage stats | `{ usage, quota, percent }` |
| `/storage-limit-alerts/all` | GET | All user alerts (admin only) | `[{ alert }]` |
| `/storage-limit-alerts/refresh` | POST | Recalculate storage from S3 | `{ updated_usage }` |
| `/storage-limit-alerts/limit-warning` | GET | Full limit warning details | `LimitWarning` or `null` |
| `/storage-limit-alerts/limit-warning/check` | GET | Quick warning status check | `LimitWarningStatus` |

### Frontend Alert Component

**File:** `frontend/src/components/common/LimitAlert.tsx`

| Alert Type | Severity | Title | Behavior |
|------------|----------|-------|----------|
| `overdue` | error (red) | "Data Cleanup Overdue" | Requires acknowledgment, no auto-dismiss |
| `storage` | warning (yellow) | "Storage Limit Exceeded" | Dismissible |
| `grace` | warning (yellow) | "Premium Subscription Expired" | Dismissible, shows countdown |

Features:
- Progress bar showing time remaining (hours or days granularity)
- Storage usage details (X GB used / Y GB limit, Z GB excess)
- Action buttons: "Upgrade to Premium" and "Manage Files"
- Cross-tab synchronization via localStorage and BroadcastChannel
- OVERDUE alerts require explicit acknowledgment before dismissal

### Key Functions Reference

| Function | File | Purpose |
|----------|------|---------|
| `calculate_limit_warning()` | `studio/app/common/core/cloud/cloud_utils.py` | Core 5-case decision tree for limit warnings |
| `_generate_subscription_warning_message()` | `studio/app/common/core/cloud/cloud_utils.py` | Context-aware message generation per status |
| `get_user_storage_usage()` | `studio/app/common/core/cloud/storage_tracking.py` | Get cached storage usage from database |
| `get_current_user_storage_usage()` | `studio/app/common/core/cloud/storage_tracking.py` | Get fresh storage usage (async, S3 scan) |
| `handle_checkout_completed()` | `studio/app/common/core/subscription/webhook_service.py` | Stripe webhook: create subscription record |
| `handle_subscription_cancelled()` | `studio/app/common/core/subscription/webhook_service.py` | Stripe webhook: clean up subscription |
| `handle_payment_failed()` | `studio/app/common/core/subscription/webhook_service.py` | Stripe webhook: mark subscription past_due |

---

## Error Handling

### Stripe API Errors

```python
try:
    subscription = stripe.Subscription.modify(...)
except stripe.error.CardError as e:
    # Card declined - notify user to update payment
    return {"error": "card_declined", "message": e.user_message}
except stripe.error.RateLimitError:
    # Too many requests - implement exponential backoff
    raise HTTPException(status_code=429, detail="Rate limited")
except stripe.error.InvalidRequestError as e:
    # Invalid parameters - log and return error
    logger.error(f"Invalid Stripe request: {e}")
    raise HTTPException(status_code=400, detail=str(e))
```

### Webhook Idempotency

```python
def handle_webhook(event: stripe.Event):
    """
    Webhooks may be delivered multiple times.
    Use event ID for idempotency.
    """
    event_id = event["id"]

    # Check if already processed
    if is_event_processed(event_id):
        logger.info(f"Event {event_id} already processed, skipping")
        return {"status": "already_processed"}

    # Process event
    result = dispatch_webhook_event(event)

    # Mark as processed
    mark_event_processed(event_id)
    return result
```

---

## Security Considerations

### PCI Compliance

- Card data never touches our servers (Stripe Elements)
- Stripe handles all PCI-DSS requirements
- Only store Stripe Customer/Subscription IDs (not card numbers)

### Webhook Security

- Verify webhook signatures using `STRIPE_WEBHOOK_SECRET`
- Reject requests without valid signatures
- Use HTTPS for all webhook endpoints

### API Key Protection

- Store `STRIPE_API_KEY` in AWS Secrets Manager
- Never expose in frontend code or logs
- Use restricted API keys where possible

---

## Files Summary

### Backend

| File | Purpose |
|------|---------|
| `studio/app/common/core/subscription/stripe_service.py` | Stripe API integration |
| `studio/app/common/core/subscription/subscription_service.py` | Business logic |
| `studio/app/common/core/subscription/webhook_service.py` | Webhook handlers |
| `studio/app/common/routers/subscription.py` | API endpoints |
| `studio/app/common/routers/webhooks.py` | Webhook receiver |

### Frontend

| File | Purpose |
|------|---------|
| `frontend/src/api/subscription/*.ts` | Subscription API calls |
| `frontend/src/components/Subscription/*.tsx` | Plan selection, billing UI |
| `frontend/src/components/Payment/*.tsx` | Payment method management |

### Infrastructure

| File | Purpose |
|------|---------|
| `infrastructure/terraform/secrets.tf` | Stripe secrets configuration |

---

## References

- [Stripe API Documentation](https://stripe.com/docs/api)
- [Stripe Webhooks Guide](https://stripe.com/docs/webhooks)
- [Stripe Elements](https://stripe.com/docs/stripe-js)
- Related: `PREMIUM_MANAGER_ARCHITECTURE.md`, `ALB_ROUTING_SECURITY.md`
