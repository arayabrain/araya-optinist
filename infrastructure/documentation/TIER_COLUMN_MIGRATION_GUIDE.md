# Tier Column Migration Guide

## Overview

This migration adds a `tier` column and related fields to the `subscription_plans` table, enabling flexible, data-driven subscription plan management. This eliminates hardcoded plan ID checks and allows easy addition of new subscription plans without code changes.

## Migration Details

**Migration File**: `studio/alembic/versions/g901g9250021_add_tier_column_to_subscription_plans.py`
**Revision ID**: `g901g9250021`
**Revises**: `f801f8250020`
**Created**: 2026-01-09

### New Columns Added

| Column | Type | Description | Default |
|--------|------|-------------|---------|
| `tier` | VARCHAR(50) | Plan tier identifier (free, premium, enterprise) | 'free' |
| `display_order` | INTEGER | Display order for UI sorting | 0 |
| `is_featured` | BOOLEAN | Whether plan is highlighted in UI | FALSE |
| `max_storage_gb` | INTEGER | Storage quota in GB | NULL |
| `description` | TEXT | Detailed plan description | NULL |
| `metadata` | JSON | Extensible metadata for future attributes | NULL |

### Indexes Created

- `idx_subscription_plans_tier` - Index on `tier` column for fast tier-based queries
- `idx_subscription_plans_display_order` - Index on `display_order` for UI sorting

## Running the Migration

### Step 1: Backup Database

**⚠️ IMPORTANT: Always backup your database before running migrations!**

```bash
# Example backup command (adjust for your environment)
mysqldump -u username -p database_name > backup_$(date +%Y%m%d_%H%M%S).sql
```

### Step 2: Run the Migration

```bash
# Navigate to project root
cd /Users/tsuchiyama/Developer/optinist-for-cloud

# Run the migration
alembic upgrade head
```

### Step 3: Verify Migration

```bash
# Check migration status
alembic current

# Expected output should show: g901g9250021 (head)
```

### Step 4: Verify Database Changes

```sql
-- Connect to your database and verify the new columns exist
DESCRIBE subscription_plans;

-- Check the tier values were set correctly
SELECT id, name, tier, max_storage_gb, display_order, is_featured
FROM subscription_plans;

-- Expected results:
-- id=1: tier='free', max_storage_gb=5, display_order=1, is_featured=0
-- id=2: tier='premium', max_storage_gb=200, display_order=2, is_featured=1
```

## Rollback (If Needed)

If you need to rollback the migration:

```bash
# Rollback to previous revision
alembic downgrade f801f8250020

# Verify rollback
alembic current
```

## Using the New Tier System

### Backend Usage

#### Old Way (Hardcoded - DO NOT USE)
```python
# ❌ BAD: Hardcoded plan ID checks
if subscription_plan_id == SubscriptionPlanIds.PREMIUM:
    # Premium logic
```

#### New Way (Data-Driven - RECOMMENDED)
```python
# ✅ GOOD: Tier-based checks
plan = db.query(SubscriptionPlans).filter(SubscriptionPlans.id == plan_id).first()
if plan and plan.tier == "premium":
    # Premium logic

# ✅ EVEN BETTER: Use the property method
if plan and plan.is_premium_tier:
    # Premium logic
```

### Example: Refactoring Hardcoded Logic

**Before (subscription_service.py:146-149):**
```python
def get_subscription_status(plan_data_id: int, is_cancelled: bool) -> int:
    if is_cancelled:
        subscription_status = SubscriptionUserStatus.CANCELED
    elif plan_data_id == SubscriptionPlanType.MONTHLY:  # ❌ Hardcoded
        subscription_status = SubscriptionUserStatus.FREE
    elif plan_data_id == SubscriptionPlanType.YEARLY:  # ❌ Hardcoded
        subscription_status = SubscriptionUserStatus.SUBSCRIBED
    else:
        subscription_status = SubscriptionUserStatus.FREE
    return subscription_status
```

**After (Recommended):**
```python
def get_subscription_status(
    db: Session,
    plan_data_id: int,
    is_cancelled: bool
) -> int:
    if is_cancelled:
        return SubscriptionUserStatus.CANCELED

    # ✅ Query plan tier from database
    plan = db.query(SubscriptionPlans).filter(
        SubscriptionPlans.id == plan_data_id
    ).first()

    if not plan:
        return SubscriptionUserStatus.FREE

    # ✅ Use tier to determine status
    if plan.tier == "free":
        return SubscriptionUserStatus.FREE
    elif plan.is_premium_tier:  # Covers premium, enterprise, etc.
        return SubscriptionUserStatus.SUBSCRIBED
    else:
        return SubscriptionUserStatus.FREE
```

## Adding New Plans

With the tier system, adding new plans is now trivial:

```sql
-- Add an Enterprise plan (example)
INSERT INTO subscription_plans (
    name,
    price,
    billing_cycle,
    features,
    currency,
    status,
    tier,
    max_storage_gb,
    display_order,
    is_featured,
    description,
    stripe_product_id,
    stripe_price_id
) VALUES (
    'Enterprise',
    49900,  -- $499.00 in cents
    2,      -- Yearly billing
    '{"Enterprise": [{"text": "Unlimited storage", "isPremium": true}]}',
    1,      -- USD
    TRUE,
    'enterprise',  -- New tier!
    NULL,   -- Unlimited storage
    3,      -- Display after premium
    FALSE,
    'Enterprise plan with unlimited storage and priority support',
    'prod_xxx',  -- Stripe product ID
    'price_xxx'  -- Stripe price ID
);
```

**No code changes needed!** The system will automatically recognize the new plan tier.

## Frontend Usage

The frontend DTO has already been updated to support the new fields:

```typescript
// frontend/src/api/subscriptions/SubscriptionsApiDTO.ts
export type SubscriptionPlanDTO = {
  id: number
  name: string
  price: number
  billing_cycle: number | string
  features: Record<string, unknown> | string[]
  currency: number | string
  status: boolean
  created_at: string
  // New optional fields (backward compatible)
  tier?: string
  stripe_product_id?: string
  stripe_price_id?: string
  description?: string
  metadata?: Record<string, unknown>
}
```

### Frontend Utilities

```typescript
// Check if plan is premium or higher
const isPremiumPlan = (plan: SubscriptionPlanDTO): boolean => {
  if (plan.tier) {
    return ['premium', 'enterprise', 'professional'].includes(plan.tier.toLowerCase())
  }
  // Fallback for backward compatibility
  return plan.name.toLowerCase().includes('premium')
}
```

## Migration Checklist

- [ ] Database backed up
- [ ] Migration file reviewed
- [ ] Migration executed successfully (`alembic upgrade head`)
- [ ] Database schema verified (`DESCRIBE subscription_plans`)
- [ ] Tier values verified for existing plans
- [ ] Application restarted (if needed)
- [ ] Endpoints tested (GET `/api/subsc/mgmts/plans`)
- [ ] Plan selection UI tested
- [ ] Subscription creation tested

## Next Steps: Refactoring Hardcoded Checks

After running this migration, you should refactor hardcoded plan ID checks throughout the codebase:

### Files to Refactor (Priority Order)

1. **High Priority** (Business Logic):
   - `studio/app/common/core/subscription/subscription_service.py:146-149`
   - `studio/app/common/core/auth/auth_dependencies.py:98-101`
   - `studio/app/common/core/users/crud_users.py:130-135, 283-286`

2. **Medium Priority** (Frontend Logic):
   - `frontend/src/utils/routing/RoutingService.ts:98`
   - Components checking `PlanName.PREMIUM`

3. **Low Priority** (Test Files):
   - Update test fixtures to use tier-based checks
   - Tests will continue to work but should be modernized

## Troubleshooting

### Migration Fails with "Column already exists"

```bash
# Check current migration version
alembic current

# If migration was partially applied, rollback and retry
alembic downgrade f801f8250020
alembic upgrade head
```

### Tier values not set correctly

```sql
-- Manually update tier values if needed
UPDATE subscription_plans SET tier = 'free', max_storage_gb = 5 WHERE id = 1;
UPDATE subscription_plans SET tier = 'premium', max_storage_gb = 200 WHERE id = 2;
```

### API returns old schema without tier field

- Ensure application was restarted after migration
- Check that `SubscriptionPlanResponse` schema includes new fields
- Verify database columns exist: `DESCRIBE subscription_plans;`

## Support

For issues or questions:
- Check the migration file: `studio/alembic/versions/g901g9250021_add_tier_column_to_subscription_plans.py`
- Review model changes: `studio/app/common/models/subscription.py`
- Review schema changes: `studio/app/common/schemas/subscriptions.py`

## Related Files

- Migration: `studio/alembic/versions/g901g9250021_add_tier_column_to_subscription_plans.py`
- Model: `studio/app/common/models/subscription.py`
- Schema: `studio/app/common/schemas/subscriptions.py`
- Frontend DTO: `frontend/src/api/subscriptions/SubscriptionsApiDTO.ts`
- Constants: `studio/app/common/core/subscription/constants.py`
