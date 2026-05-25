# Multiple Subscription Plans

## Overview

The subscription system uses a **data-driven, tier-based architecture** that allows adding new subscription plans without code changes. Plans are stored in the `subscription_plans` database table and configured via Terraform/seed scripts during deployment.

## Current Plans

| ID | Name    | Price     | Tier    | Storage | display_order |
|----|---------|-----------|---------|---------|---------------|
| 1  | Free    | $0/month  | free    | 5 GB    | 0             |
| 2  | Premium | $20/month | premium | 200 GB  | 1             |

## Plan Schema

Each plan has the following fields:

| Field              | Type    | Description                                          |
|--------------------|---------|------------------------------------------------------|
| `id`               | int     | Unique plan identifier                               |
| `name`             | string  | Display name (e.g., "Free", "Premium")               |
| `price`            | int     | Price in cents (0 = free plan)                       |
| `billing_cycle`    | int     | 1 = monthly, 2 = yearly                             |
| `currency`         | int     | 1 = USD, 2 = JPY                                    |
| `status`           | bool    | true = active, false = inactive                      |
| `tier`             | string  | Tier identifier (e.g., "free", "premium")            |
| `display_order`    | int     | UI sort order (lower values appear first)            |
| `is_featured`      | bool    | Highlighted in UI with visual emphasis               |
| `is_hidden`        | bool    | Hidden from plan selection UI                        |
| `stripe_product_id`| string  | Stripe product ID for payment integration            |
| `stripe_price_id`  | string  | Stripe price ID for payment integration              |
| `features`         | JSON    | Feature list displayed on plan cards                 |
| `storage_quota_gb` | int     | Storage quota in GB (used by seed script)            |

## How Tier-Based Logic Works

The system determines plan type by checking the `tier` field rather than hardcoded plan IDs:

- **Free plan**: Any plan where `price == 0` (or `tier == "free"`)
- **Paid plan**: Any plan where `tier != "free"` (extensible to any tier name)

This means adding a new tier (e.g., "enterprise", "starter") requires only a database insert -- no code changes.

## Display Order Management

`display_order` controls the sort order of plans in the UI. Lower values appear first.

**Current approach**: `display_order` is managed via direct SQL updates.

Since the number of plans is small and changes are infrequent, SQL updates are sufficient for managing display order. The seed script also supports setting `display_order` during initial deployment.

```sql
-- Example: Set display order for existing plans
UPDATE subscription_plans SET display_order = 0 WHERE id = 1;  -- Free first
UPDATE subscription_plans SET display_order = 1 WHERE id = 2;  -- Premium second

-- Example: Insert a new plan with display order
INSERT INTO subscription_plans (
  name, price, billing_cycle, tier, display_order,
  is_featured, is_hidden, features, currency, status,
  stripe_product_id, stripe_price_id
) VALUES (
  'Enterprise', 5000, 1, 'enterprise', 2,
  true, false, '{"Enterprise": [...]}', 1, true,
  'prod_xxx', 'price_xxx'
);
```

**Future consideration**: If plan management becomes more frequent, display order and other plan metadata could be synced from Stripe product metadata to centralize plan configuration. However, keeping synchronization logic simple is preferred over complex sync mechanisms.

## Adding a New Plan

### 1. Create the plan in Stripe

Create a new product and price in Stripe Dashboard. Note the `prod_xxx` and `price_xxx` IDs.

### 2. Add to Terraform configuration

Add the plan to `subscription_plans` in the environment tfvars file:

```hcl
{
  id                = 3
  name              = "Enterprise"
  price             = 5000
  billing_cycle     = 1
  currency          = 1
  status            = 1
  stripe_product_id = "prod_xxx"
  stripe_price_id   = "price_xxx"
  storage_quota_gb  = 500
  display_order     = 2
  is_featured       = true
  tier              = "enterprise"
  features = {
    Enterprise = [
      { text = "Everything in Premium", isPremium = false },
      { text = "500GB storage",         isPremium = true  },
    ]
  }
}
```

### 3. Deploy

Run `terraform apply` to update the infrastructure. The seed script runs automatically during deployment and upserts the plan into the database.

### 4. Verify

After deployment, the new plan should appear in the plan selection UI, sorted by `display_order`.

## Plan Visibility

- **`is_hidden = true`**: Plan exists in the database but is not shown in the plan selection UI. Useful for legacy plans or plans being prepared for future launch.
- **`is_featured = true`**: Plan card is visually highlighted in the UI (scaled up with accent border).

## API Endpoints

| Method | Path                       | Description                    |
|--------|----------------------------|--------------------------------|
| GET    | `/api/subsc/mgmts/plans`   | Returns active, visible plans sorted by `display_order` |
| GET    | `/api/subsc/mgmts`         | Get user's current subscription |

## Architecture Notes

- **Backend**: `SubscriptionService.get_active_plans()` filters by `status=active` and `is_hidden=false`, ordered by `display_order`.
- **Frontend**: Uses `sortPlans()` utility to sort by `display_order` (primary) or `price` (fallback). Filters out hidden plans.
- **Seed script**: `infrastructure/scripts/seed_subscription_plans.py` reads plan config from `SUBSCRIPTION_PLANS_CONFIG` environment variable and upserts plans into the database.
- **Terraform**: Plan definitions live in `infrastructure/terraform/environments/*.tfvars`. The variable schema is defined in `infrastructure/terraform/main.tf`.
