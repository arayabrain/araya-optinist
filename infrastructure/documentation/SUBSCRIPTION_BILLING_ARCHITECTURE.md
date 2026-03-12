# Subscription Billing: Payment and Lifecycle Management

## Executive Summary

- **StripeService** handles direct Stripe API integration for payment methods and subscriptions
- **SubscriptionService** manages business logic, subscription state, and database operations
- **WebhookService** processes real-time Stripe webhook notifications for billing events
- **Webhook-driven architecture** ensures subscription state stays consistent via event handlers rather than polling
- **Grace period system** provides 30-day grace + 30-day warning after premium expiration before data cleanup
- **Scheduled plan changes** use Stripe SubscriptionSchedules to prevent race conditions during upgrades/downgrades

---

## Key Architectural Principles

1. **Webhook-Driven State Management**
   - Subscription state changes are driven by Stripe webhook events, not direct API calls
   - Database updates happen in webhook handlers to ensure consistency with Stripe's source of truth
   - Webhook signature verification prevents spoofed events

2. **Scheduled Changes Over Immediate Mutations**
   - Plan changes use Stripe SubscriptionSchedules rather than modifying active subscriptions
   - Eliminates race conditions between concurrent upgrade/downgrade requests
   - Database updates deferred to `subscription_schedule.released` webhook

3. **Graceful Degradation After Expiration**
   - Premium users get 30-day grace period with full access after subscription expires
   - Additional 30-day warning period before data cleanup
   - Storage alerts only appear when usage exceeds the free tier limit (5 GB)

4. **PCI Compliance by Design**
   - Card data never touches application servers (Stripe Elements handles collection)
   - Only Stripe Customer/Subscription IDs stored in database
   - SetupIntents used for secure payment method attachment

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

## Implementation Details

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
│ 2. Frontend calls: POST /api/subsc/payment-methods/setup-intent          │
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
│ 4. Frontend calls: PUT /api/subsc/payment-methods                        │
│    → Backend attaches PaymentMethod to Customer                         │
│    → Sets as default for subscription                                   │
└─────────────────────────────────────────────────────────────────────────┘
```

#### handle_update_user_subscription()

**File:** `studio/app/common/core/subscription/stripe_service.py`
**Purpose:** Update a user's subscription to a different plan using webhook-driven database updates
**Input:** `db` (Session), `user` (User), `request` (UpdateSubscriptionRequest containing new_plan_id)
**Output:** `UpdateSubscriptionResponse` with success status, change_type (upgrade/downgrade), effective_date, and proration info
**Calls:** `stripe.Subscription.modify()` -> `stripe.SubscriptionSchedule.create()` -> `update_scheduled_downgrade()`

Plan changes are scheduled at billing period end using Stripe SubscriptionSchedules rather than modifying
the active subscription directly. Database updates are deferred to the `subscription_schedule.released`
webhook handler.

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
  (see `studio/app/common/schemas/users.py:has_active_subscription`,
  `frontend/src/utils/routing/RoutingService.ts`)
- **Workflow execution** is allowed as long as storage stays under quota
  (the same quota check that applies to all statuses)
- **Alerts** warn the user that their subscription has expired and
  show a countdown of grace days remaining
- After the 30-day grace period, the status transitions to `EXPIRED`
  and access drops to Free tier (5 GB quota, no premium compute)

#### calculate_limit_warning()

**File:** `studio/app/common/core/cloud/cloud_utils.py`
**Purpose:** Core 5-case decision tree that evaluates subscription status and storage usage to determine user alerts
**Input:** `user_id` (int)
**Output:** `Optional[LimitWarning]` with alert_type, days_remaining, excess_data_bytes, and warning message; returns `None` when no alert is needed
**Calls:** `get_user_storage_usage()` -> `get_current_user_storage_usage()` -> `_generate_subscription_warning_message()`

Uses cached storage data if fresh (< 20 minutes), otherwise triggers a live S3 scan. Compares storage
against the effective quota (200 GB for active premium, 5 GB for grace/warning/overdue/free) and returns
the appropriate alert type with a countdown of days remaining.

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
│ POST /api/subsc/webhooks/stripe                                          │
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

#### handle_checkout_completed()

**File:** `studio/app/common/core/subscription/webhook_service.py`
**Purpose:** Process successful checkout session completion from Stripe webhook
**Input:** `db` (Session), `session_data` (dict with Stripe session object containing customer, subscription, and metadata)
**Output:** Dict with success status, subscription_user_id, purchase_id, expiration_date
**Calls:** `stripe.Subscription.retrieve()` -> database insert (UserSubscription, SubscriptionUserPurchase)

Includes duplicate detection within a 30-minute window to handle webhook redelivery. Uses a multi-layer
fallback chain for calculating expiration dates (trial_end -> current_period_end -> period_start + 1 month
-> latest invoice).

#### handle_payment_failed()

**File:** `studio/app/common/core/subscription/webhook_service.py`
**Purpose:** Handle failed invoice payment webhook from Stripe
**Input:** `db` (Session), `invoice_data` (dict with Stripe invoice object containing customer ID)
**Output:** None
**Calls:** `invalidate_user_tier_cache()`

Marks the subscription's `sync_status` as `FAILED` and invalidates the user tier cache for immediate
warning display. Does not downgrade the subscription -- the user keeps premium access during Stripe's
automatic retry window. If all retries fail, Stripe sends a `customer.subscription.deleted` event.

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
| `/api/subsc/mgmts/plans` | GET | List available plans |
| `/api/subsc/mgmts` | GET | Get current user's subscription |
| `/api/subsc/mgmts` | PUT | Update subscription plan |
| `/api/subsc/mgmts/cancel` | DELETE | Cancel subscription |
| `/api/subsc/checkout/create-checkout-session` | POST | Create checkout session |

### Payment Endpoints

| Endpoint | Method | Purpose | Status |
|----------|--------|---------|--------|
| `/api/subsc/payment-methods` | GET | List payment methods | Not implemented (501) |
| `/api/subsc/payment-methods/default` | GET | Get default payment method | Active |
| `/api/subsc/payment-methods` | PUT | Set default payment method | Not implemented (501) |
| `/api/subsc/payment-methods/{id}` | DELETE | Remove payment method | Not implemented (501) |
| `/api/subsc/payment-methods/setup-intent` | POST | Create SetupIntent | Not implemented (501) |

### Webhook Endpoint

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/subsc/webhooks/stripe` | POST | Receive Stripe webhooks |

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
        "storage_gb": 200,
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
       │   (no warning needed)   │  PERIOD   │  Premium access retained (200 GB)
       │ <────────────────────── │           │  Alerts measure against 5 GB free limit
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
    │  "Subscription │  "Data deleted  │ "Scheduled    │
    │   expired,     │   in X days"   │  for deletion"│
    │   X days left" │                 │               │
    └────────────────┴─────────────────┴───────────────┘

  Key dates computed by calculate_limit_warning():
    subscription_end  = UserSubscription.expiration
    grace_end         = subscription_end + 30 days (GRACE_PERIOD_DAYS)
    deletion_date     = grace_end + 30 days (WARNING_PERIOD_DAYS)
```

### Subscription Lifecycle States

| Status | Duration | Storage Quota | Alert Threshold | Alert Type | User Experience |
|--------|----------|---------------|-----------------|------------|-----------------|
| `FREE` | Indefinite | 5 GB | 5 GB | `storage` (if over) | Full free features |
| `ACTIVE` | Billing period | 200 GB | 200 GB | `storage` (if over) | Full premium features |
| `GRACE` | Days 0-30 | 200 GB (retained) | 5 GB | `grace` | Full premium access, countdown warning |
| `WARNING` | Days 30-60 | 5 GB | 5 GB | `grace` | "Data will be deleted in X days" |
| `OVERDUE` | Day 60+ | 5 GB | 5 GB | `overdue` | "Data scheduled for deletion" |

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
    subscription_end_date: Optional[str]  # ISO date when premium expired
    grace_end_date: Optional[str]         # ISO date when grace period ends (T+30d)
    deletion_date: Optional[str]          # ISO date when data deletion begins (T+60d)
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

## Edge Case Handling

### 1. Duplicate Webhook Delivery

**Problem:** Stripe may deliver the same webhook event multiple times, causing duplicate subscription records or purchases.

**Solution:** Checkout handler uses a 30-minute duplicate detection window:
- Queries for existing purchases with the same user_id and plan_id within the last 30 minutes
- If a duplicate is detected but the subscription is still active, returns success immediately
- If the subscription has expired/cancelled since the original purchase, allows the new purchase to proceed

### 2. Webhook References Non-Existent User

**Problem:** Webhook events may reference Stripe customers that don't exist in the database (test data, incomplete checkouts, deleted users).

**Solution:** Returns HTTP 200 to prevent infinite Stripe retries:
- Logs a warning with the unrecognized customer_id
- Returns `{"success": True, "skipped": True, "reason": "missing_user_account"}`
- Stripe stops retrying, avoiding alert fatigue

### 3. Subscription Not Found During Payment Event

**Problem:** Payment success/failure webhooks may arrive when no matching subscription exists (e.g., trial-to-paid transitions with timing gaps).

**Solution:** Three-tier subscription lookup fallback:
- First: active subscriptions (expiration > now)
- Then: recently expired subscriptions (within 30-day window)
- Finally: any non-free subscription (ordered by most recent expiration)

### 4. Stale Storage Data During Alert Calculation

**Problem:** Storage usage data may be outdated, causing inaccurate limit warnings.

**Solution:** `calculate_limit_warning()` uses a freshness check:
- Uses cached storage data if less than 20 minutes old (`MAX_CACHE_AGE_MINUTES`)
- Triggers a live S3 scan when cached data is stale or missing
- Prevents excessive S3 API calls while maintaining accuracy

### 5. Payment Failure During Active Subscription

**Problem:** A card decline could immediately cut off a user's premium access.

**Solution:** Graceful degradation during retry window:
- Subscription `sync_status` set to `FAILED` (tracking only)
- User retains full premium access during Stripe's automatic retry period
- User tier cache invalidated for immediate warning display
- Only after all retries are exhausted does Stripe send `customer.subscription.deleted`

---

## Monitoring and Metrics

The subscription system uses application-level logging for observability. No custom CloudWatch metrics are published.

### Log Levels

| Level | Usage | Example |
|-------|-------|---------|
| `INFO` | Significant operations (checkout, plan change, cache invalidation) | `"Successfully processed checkout for user {user_id}"` |
| `WARNING` | Degraded operations (payment failure, missing user, Stripe not initialized) | `"Payment failed for customer: {customer_id}"` |
| `ERROR` | Failures (Stripe API errors, database errors, calculation failures) | `"Failed to calculate limit warning for user {user_id}"` |
| `DEBUG` | Development context (test mode skips, missing subscriptions) | `"Skipping storage usage lookup for user {user_id} (test mode)"` |

### Key Observability Points

| Event | Log Level | Location |
|-------|-----------|----------|
| Checkout completed | INFO | `webhook_service.py` |
| Payment failed | WARNING | `webhook_service.py` |
| Subscription cancelled | INFO | `webhook_service.py` |
| Duplicate webhook detected | INFO | `webhook_service.py` |
| Unknown customer in webhook | WARNING | `webhook_service.py` |
| Storage cache miss (S3 scan triggered) | INFO | `cloud_utils.py` |
| Limit warning calculation failure | ERROR | `cloud_utils.py` |
| User tier cache invalidated | INFO | `stripe_service.py` |

### Database State Tracking

The `sync_status` field on `UserSubscription` tracks webhook processing state:
- `SYNCED` -- subscription state matches Stripe
- `FAILED` -- payment failure detected, awaiting Stripe retry

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
| `studio/app/common/routers/subscriptions.py` | API and webhook endpoints |

### Frontend

| File | Purpose |
|------|---------|
| `frontend/src/api/subscriptions/Subscriptions.ts` | Subscription API calls |
| `frontend/src/api/paymentMethod/PaymentMethod.ts` | Payment method API calls |
| `frontend/src/pages/Subscription/*.tsx` | Plan selection, billing UI |
| `frontend/src/components/common/LimitAlert.tsx` | Storage/grace period alerts |

### Infrastructure

| File | Purpose |
|------|---------|
| `infrastructure/terraform/security.tf` | Stripe secrets (AWS Secrets Manager) |

---

## References

- [Stripe API Documentation](https://stripe.com/docs/api)
- [Stripe Webhooks Guide](https://stripe.com/docs/webhooks)
- [Stripe Elements](https://stripe.com/docs/stripe-js)
- Related: `PREMIUM_MANAGER_ARCHITECTURE.md`, `ALB_ROUTING_SECURITY.md`
