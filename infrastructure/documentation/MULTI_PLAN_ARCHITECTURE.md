# Multi-Plan Architecture Guide

## Overview

OptiNist now supports a flexible, tier-based subscription system that can handle unlimited subscription plans without code changes. The system uses a **data-driven approach** where plan tiers (free, premium, enterprise, etc.) are stored in the database and queried at runtime.

## Key Principles

### 1. Tier-Based Logic (Not ID-Based)

**❌ OLD WAY (Hardcoded):**
```python
if plan_id == 1:  # Free
    storage = 5GB
elif plan_id == 2:  # Premium
    storage = 200GB
```

**✅ NEW WAY (Data-Driven):**
```python
plan = db.query(SubscriptionPlans).filter(SubscriptionPlans.id == plan_id).first()
if plan.tier == "free":
    storage = plan.max_storage_gb
elif plan.is_premium_tier:  # Covers premium, enterprise, professional, etc.
    storage = plan.max_storage_gb
```

### 2. Dynamic Plan Discovery

Use the new helper methods in `SubscriptionService`:

```python
# Get a plan by tier
free_plan = SubscriptionService.get_plan_by_tier(db, "free")
premium_plan = SubscriptionService.get_plan_by_tier(db, "premium")
enterprise_plan = SubscriptionService.get_plan_by_tier(db, "enterprise")

# Get the default free plan ID for new users
free_plan_id = SubscriptionService.get_default_plan_id(db)
```

### 3. Tier Property for Easy Checking

The `SubscriptionPlans` model has a convenient property:

```python
plan = db.query(SubscriptionPlans).first()

if plan.is_premium_tier:
    # True for premium, enterprise, professional, and any future premium tiers
    grant_premium_features()
```

## Plan Structure

### Database Schema

```sql
CREATE TABLE subscription_plans (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL,
    price INT NOT NULL,  -- In cents
    billing_cycle INT NOT NULL,  -- 1=Monthly, 2=Yearly
    features JSON NOT NULL,
    currency INT NOT NULL DEFAULT 1,  -- 1=USD, 2=JPY
    status BOOLEAN NOT NULL DEFAULT TRUE,

    -- New tier-based fields
    tier VARCHAR(50) NOT NULL DEFAULT 'free',
    display_order INT NOT NULL DEFAULT 0,
    is_featured BOOLEAN NOT NULL DEFAULT FALSE,
    max_storage_gb INT NULL,  -- NULL = unlimited
    description TEXT NULL,
    metadata JSON NULL,

    -- Stripe integration
    stripe_product_id VARCHAR(255) NULL,
    stripe_price_id VARCHAR(255) NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_subscription_plans_tier (tier),
    INDEX idx_subscription_plans_display_order (display_order)
);
```

### Tier Types

- **free**: No-cost basic plan (typically 5GB storage)
- **premium**: Paid individual/small team plan (typically 200GB storage)
- **enterprise**: High-tier organizational plan (typically unlimited storage)
- **professional**: Optional mid-tier plan
- *Custom tiers*: You can add any tier name you want!

## Adding New Plans

### Method 1: Direct SQL Insert

```sql
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
    'Professional',         -- Plan name
    2900,                  -- $29.00 (in cents)
    1,                     -- Monthly billing
    '{"Professional": [{"text": "100GB storage", "isPremium": true}, {"text": "Priority support", "isPremium": true}]}',
    1,                     -- USD
    TRUE,                  -- Active
    'professional',        -- Tier (new tier type!)
    100,                   -- 100GB storage
    2,                     -- Display between free and premium
    FALSE,                 -- Not featured
    'Professional plan with 100GB storage and priority support',
    'prod_xyz123',         -- Stripe product ID
    'price_xyz456'         -- Stripe price ID
);
```

### Method 2: Python Script

```python
from studio.app.common.models.subscription import SubscriptionPlans
from studio.app.common.db.database import get_db

db = next(get_db())

new_plan = SubscriptionPlans(
    name="Professional",
    price=2900,  # $29.00
    billing_cycle=1,  # Monthly
    features={"Professional": [
        {"text": "100GB storage", "isPremium": True},
        {"text": "Priority support", "isPremium": True}
    ]},
    currency=1,  # USD
    status=True,
    tier="professional",
    max_storage_gb=100,
    display_order=2,
    is_featured=False,
    description="Professional plan with 100GB storage and priority support",
    stripe_product_id="prod_xyz123",
    stripe_price_id="price_xyz456"
)

db.add(new_plan)
db.commit()
```

**No code changes needed!** The system automatically recognizes the new tier.

## Code Examples

### Getting User's Plan Tier

```python
# In auth_dependencies.py or any service
plan = db.query(SubscriptionPlans).filter(
    SubscriptionPlans.id == subscription_plan_id
).first()

if plan.tier == "free":
    # Free tier logic
    apply_storage_limit(5)
elif plan.tier == "professional":
    # Professional tier logic
    apply_storage_limit(100)
elif plan.is_premium_tier:  # Covers premium, enterprise, professional
    # Premium+ tier logic
    apply_storage_limit(plan.max_storage_gb)
```

### Creating Users with Default Plan

```python
# In crud_users.py
free_plan_id = SubscriptionService.get_default_plan_id(db)
subscription = UserSubscription(
    plan_id=free_plan_id,  # Dynamic lookup!
    user_id=user_db.id,
    expiration=datetime.utcnow()
)
```

### Middleware Tier Check

```python
# In secure_routing_middleware.py
subscription_data = SubscriptionService.get_user_subscription(db, user.id)
if subscription_data:
    subscription, plan = subscription_data
    tier = plan.tier  # Use tier directly from plan
else:
    tier = "free"
```

## Migration Guide

### For Existing Deployments

1. **Run the SQL migration:**
   ```bash
   mysql -u <user> -p <database> < add_tier_columns.sql
   ```

2. **Restart your application** to pick up the new schema.

3. **Verify plans have tiers:**
   ```sql
   SELECT id, name, tier, max_storage_gb, display_order
   FROM subscription_plans;
   ```

4. **All existing code automatically uses the new tier system** - no additional changes needed!

### Creating Additional Plans

After migration, you can add as many plans as you want:

```sql
-- Add a Team plan
INSERT INTO subscription_plans (
    name, price, billing_cycle, features, currency, status,
    tier, max_storage_gb, display_order, description,
    stripe_product_id, stripe_price_id
) VALUES (
    'Team', 4900, 1, '{"Team": [{"text": "500GB storage", "isPremium": true}]}',
    1, TRUE, 'team', 500, 3,
    'Team plan with 500GB shared storage',
    'prod_team', 'price_team'
);

-- Add an Enterprise Annual plan
INSERT INTO subscription_plans (
    name, price, billing_cycle, features, currency, status,
    tier, max_storage_gb, display_order, description,
    stripe_product_id, stripe_price_id
) VALUES (
    'Enterprise Annual', 99900, 2, '{"Enterprise": [{"text": "Unlimited storage", "isPremium": true}]}',
    1, TRUE, 'enterprise', NULL, 4,
    'Enterprise annual plan with unlimited storage',
    'prod_enterprise_annual', 'price_enterprise_annual'
);
```

## Benefits of Multi-Plan Architecture

### ✅ Flexibility
- Add unlimited plans without touching code
- Support multiple price points and tiers
- Easy A/B testing of pricing

### ✅ Maintainability
- No hardcoded plan IDs scattered throughout codebase
- Single source of truth (database)
- Less technical debt

### ✅ Scalability
- Support enterprise customers with custom plans
- Regional pricing variations
- Promotional/limited-time plans

### ✅ Data-Driven
- Query plan characteristics at runtime
- Dynamic feature gating based on tier
- Easy to update plan details

## Common Patterns

### Pattern 1: Feature Gating

```python
def can_use_feature(db: Session, user_id: int, feature: str) -> bool:
    subscription_data = SubscriptionService.get_user_subscription(db, user_id)
    if not subscription_data:
        return False

    subscription, plan = subscription_data

    # Define feature requirements by tier
    tier_features = {
        "free": ["basic_analysis"],
        "professional": ["basic_analysis", "advanced_analysis"],
        "premium": ["basic_analysis", "advanced_analysis", "exports"],
        "enterprise": ["basic_analysis", "advanced_analysis", "exports", "api_access"]
    }

    allowed_features = tier_features.get(plan.tier, [])
    return feature in allowed_features
```

### Pattern 2: Storage Quota Enforcement

```python
def check_storage_quota(db: Session, user_id: int, additional_bytes: int) -> bool:
    storage = db.query(UserStorageUsage).filter(
        UserStorageUsage.user_id == user_id
    ).first()

    if not storage:
        return False

    # quota_bytes comes from plan.max_storage_gb (NULL = unlimited)
    if storage.storage_quota_bytes is None:
        return True  # Unlimited

    new_usage = storage.storage_usage_bytes + additional_bytes
    return new_usage <= storage.storage_quota_bytes
```

### Pattern 3: Plan Recommendations

```python
def recommend_upgrade(db: Session, user_id: int) -> Optional[SubscriptionPlans]:
    """Recommend next tier plan based on current usage."""
    current_subscription = SubscriptionService.get_user_subscription(db, user_id)
    if not current_subscription:
        return SubscriptionService.get_plan_by_tier(db, "premium")

    _, current_plan = current_subscription

    # Get next tier
    next_tier_map = {
        "free": "professional",
        "professional": "premium",
        "premium": "enterprise"
    }

    next_tier = next_tier_map.get(current_plan.tier)
    if next_tier:
        return SubscriptionService.get_plan_by_tier(db, next_tier)

    return None
```

## Testing

### Unit Test Example

```python
def test_multiple_plans(db: Session):
    # Create test plans
    free_plan = SubscriptionPlans(
        name="Free Test", price=0, tier="free",
        max_storage_gb=5, billing_cycle=1,
        features={}, currency=1, status=True
    )

    pro_plan = SubscriptionPlans(
        name="Pro Test", price=1900, tier="professional",
        max_storage_gb=100, billing_cycle=1,
        features={}, currency=1, status=True
    )

    enterprise_plan = SubscriptionPlans(
        name="Enterprise Test", price=9900, tier="enterprise",
        max_storage_gb=None, billing_cycle=1,
        features={}, currency=1, status=True
    )

    db.add_all([free_plan, pro_plan, enterprise_plan])
    db.commit()

    # Test dynamic lookup
    found_free = SubscriptionService.get_plan_by_tier(db, "free")
    assert found_free.name == "Free Test"

    found_pro = SubscriptionService.get_plan_by_tier(db, "professional")
    assert found_pro.max_storage_gb == 100

    found_ent = SubscriptionService.get_plan_by_tier(db, "enterprise")
    assert found_ent.max_storage_gb is None  # Unlimited
```

## Troubleshooting

### Issue: "No free plan found"

**Cause:** Database has no plan with `tier = 'free'`

**Fix:**
```sql
-- Ensure at least one free plan exists
INSERT INTO subscription_plans (name, price, tier, max_storage_gb, billing_cycle, features, currency, status)
VALUES ('Free', 0, 'free', 5, 1, '{}', 1, TRUE);
```

### Issue: "Plan tier is NULL"

**Cause:** Migration hasn't been run or partially failed

**Fix:**
```sql
-- Update plans without tier
UPDATE subscription_plans
SET tier = CASE
    WHEN price = 0 THEN 'free'
    WHEN LOWER(name) LIKE '%premium%' THEN 'premium'
    ELSE 'professional'
END
WHERE tier IS NULL OR tier = '';
```

### Issue: "Multiple plans with same tier"

**Behavior:** `get_plan_by_tier()` returns the lowest-priced plan

**Solution:** This is expected. Use `display_order` or specific queries if you need a different plan.

## Summary

The multi-plan architecture makes OptiNist subscription management:
- **Flexible**: Add unlimited plans without code changes
- **Maintainable**: No hardcoded IDs, single source of truth
- **Scalable**: Support any pricing model or tier structure
- **Data-Driven**: All business logic queries the database

For questions or issues, refer to:
- `studio/app/common/models/subscription.py` - Data models
- `studio/app/common/core/subscription/subscription_service.py` - Helper methods
- `TIER_COLUMN_MIGRATION_GUIDE.md` - Original migration documentation
