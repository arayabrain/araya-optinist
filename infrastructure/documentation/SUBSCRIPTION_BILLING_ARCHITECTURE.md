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
| `LIMIT_GRACE` | Over storage limit, grace period | Read-only (30 days) |
| `CANCELLED` | Scheduled for cancellation | Full until period end |
| `UNPAID` | Payment failed, all retries exhausted | Restricted access |

#### Grace Period Logic

```python
def calculate_limit_warning(user_id: int) -> dict:
    """
    Check if user has exceeded free plan limits after subscription ends.

    Returns warning info including:
    - warning_type: "storage" or "workflow"
    - days_remaining: Days until data deletion
    - excess_data_bytes: Amount over limit
    - deletion_date: When data will be purged
    """
    storage_info = get_user_storage_usage(user_id)

    # Check if user has exceeded free tier limits
    if storage_usage > FREE_TIER_STORAGE_LIMIT:
        grace_end = subscription_end + timedelta(days=30)
        return {
            "warning_type": "storage",
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
