# Frontend Multi-Plan Architecture Guide

## Overview

The frontend now supports a flexible, tier-based subscription system that can display and handle unlimited subscription plans without code changes. This matches the backend's data-driven approach.

## Key Changes

### 1. **Updated Data Types** (`SubscriptionType.ts`)

Added tier-based fields to `SubscriptionPlan` interface:

```typescript
export interface SubscriptionPlan {
  // Existing fields
  id: number
  name: string
  price: number
  billing_cycle: number
  features: Record<string, PlanFeature[]>
  currency: number
  status: boolean
  created_at: string

  // New tier-based fields (all optional for backwards compatibility)
  tier?: string                      // 'free', 'premium', 'enterprise', etc.
  display_order?: number             // Sort order for UI
  is_featured?: boolean              // Highlight plan in UI
  max_storage_gb?: number | null     // Storage quota (null = unlimited)
  description?: string               // Plan description
  stripe_product_id?: string         // Stripe integration
  stripe_price_id?: string           // Stripe integration
  metadata?: Record<string, unknown> // Extensible metadata
}
```

### 2. **New Utility Functions** (`SubscriptionUtils.ts`)

Added tier-based helper functions:

```typescript
// Check if plan is free tier
isFreePlan(plan: SubscriptionPlan): boolean

// Check if plan is premium or higher
isPremiumTierPlan(plan: SubscriptionPlan): boolean

// Get user-friendly tier name
getPlanTierDisplayName(plan: SubscriptionPlan): string

// Compare plans for upgrades/downgrades
isUpgrade(planA: SubscriptionPlan, planB: SubscriptionPlan): boolean
isDowngrade(planA: SubscriptionPlan, planB: SubscriptionPlan): boolean

// Get storage display text
getStorageDisplayText(plan: SubscriptionPlan): string

// Sort plans by display order or price
sortPlans(plans: SubscriptionPlan[]): SubscriptionPlan[]
```

### 3. **Extended Constants** (`Subscription.ts`)

Added support for multiple tier types:

```typescript
export enum PlanName {
  FREE = "Free",
  PREMIUM = "Premium",
  PROFESSIONAL = "Professional",
  ENTERPRISE = "Enterprise",
  TEAM = "Team",
  UNKNOWN = "Unknown",
}

export enum UserTier {
  FREE = "free",
  PREMIUM = "premium",
  PROFESSIONAL = "professional",
  ENTERPRISE = "enterprise",
  TEAM = "team",
}

// Helper to convert tier string to display name
export const getPlanNameFromTier = (tier?: string): string
```

## Migration from Old to New

### Before (Hardcoded)

```typescript
// ❌ OLD: Hardcoded checks
if (plan.price === 0) {
  return PlanName.FREE
}

if (plan.name === PlanName.PREMIUM) {
  highlightPlan()
}

const isDowngrade = plan.price === 0
```

### After (Tier-Based)

```typescript
// ✅ NEW: Tier-based checks
if (isFreePlan(plan)) {
  return "Free"
}

if (plan.is_featured) {
  highlightPlan()
}

const isDowngrade = isFreePlan(plan)
```

## Component Updates

### Subscription Page

**Before:**
```typescript
// Hardcoded checks
const isDowngrade = (planId: number) => {
  const plan = plans.find((p) => p.id === planId)
  return plan?.price === 0
}

// Hardcoded highlighting
<PlanCard isHighlighted={plan.name === PlanName.PREMIUM}>
```

**After:**
```typescript
// Tier-based checks
const isDowngradePlan = (planId: number) => {
  const plan = plans.find((p) => p.id === planId)
  return plan ? isFreePlan(plan) : false
}

// Dynamic highlighting based on is_featured field
<PlanCard isHighlighted={plan.is_featured || false}>

// Plans are sorted by display_order
const activePlans = sortPlans(plans.filter((plan) => plan.status === true))
```

### Account Page

**Before:**
```typescript
{userSubscription?.plan_name || PlanName.FREE}
```

**After:**
```typescript
{userSubscription?.plan_name || "Free"}
```

## Adding New Plans (Frontend)

The frontend automatically adapts to new plans from the backend. No code changes needed!

### Example: Backend adds "Professional" plan

```sql
-- Backend adds new plan
INSERT INTO subscription_plans (
  name, price, tier, display_order, is_featured, max_storage_gb
) VALUES (
  'Professional', 2900, 'professional', 2, FALSE, 100
);
```

**Frontend automatically:**
1. ✅ Displays the new plan in sorted order (by `display_order`)
2. ✅ Shows correct pricing and features
3. ✅ Handles upgrades/downgrades correctly
4. ✅ Highlights featured plans
5. ✅ Shows storage quota

## Testing Multiple Plans

### Test Scenario 1: Free → Professional → Premium

```typescript
const plans = [
  { id: 1, name: "Free", price: 0, tier: "free", display_order: 1 },
  { id: 2, name: "Professional", price: 2900, tier: "professional", display_order: 2 },
  { id: 3, name: "Premium", price: 4900, tier: "premium", display_order: 3, is_featured: true },
]

// Sort plans
const sorted = sortPlans(plans)
// Result: [Free, Professional, Premium]

// Check tiers
isFreePlan(plans[0]) // true
isPremiumTierPlan(plans[1]) // true
isPremiumTierPlan(plans[2]) // true

// Check upgrades
isUpgrade(plans[2], plans[1]) // true (Premium > Professional)
isDowngrade(plans[0], plans[1]) // true (Free < Professional)
```

### Test Scenario 2: Enterprise with Unlimited Storage

```typescript
const enterprise = {
  id: 4,
  name: "Enterprise",
  price: 19900,
  tier: "enterprise",
  max_storage_gb: null, // null = unlimited
  is_featured: false,
}

getStorageDisplayText(enterprise) // "Unlimited"
isPremiumTierPlan(enterprise) // true
```

## UI Features

### 1. **Plan Sorting**

Plans are automatically sorted by:
1. `display_order` field (if available)
2. Price (free first, then ascending)

```typescript
const sortedPlans = sortPlans(plans)
// Renders in correct order automatically
```

### 2. **Featured Plans**

Plans with `is_featured: true` are highlighted:

```typescript
<PlanCard isHighlighted={plan.is_featured || false}>
```

### 3. **Dynamic Pricing Display**

Handles any price and billing cycle:

```typescript
const getPriceDisplay = (plan: SubscriptionPlan) => {
  if (plan.price === 0) return "Free"

  const symbol = getCurrencySymbol(plan.currency)
  const cycle = getBillingCycleText(plan.billing_cycle)
  const price = (plan.price / 100).toFixed(0)

  return `${symbol}${price}/${cycle}`
}

// Examples:
// $29/month
// $299/year
// ¥2,900/month
```

### 4. **Storage Display**

Shows storage quota from plan:

```typescript
getStorageDisplayText(plan)
// "5GB"
// "200GB"
// "Unlimited"
```

## Backwards Compatibility

All changes are backwards compatible:

1. **Optional fields**: All new tier fields are optional
2. **Fallback logic**: Functions fall back to price-based checks
3. **Legacy plans**: Old plans without `tier` field still work

```typescript
// Works with old plans (no tier field)
const oldPlan = { id: 1, price: 0, name: "Free" }
isFreePlan(oldPlan) // true (falls back to price check)

// Works with new plans (tier field)
const newPlan = { id: 1, price: 0, tier: "free", name: "Free" }
isFreePlan(newPlan) // true (uses tier field)
```

## Common Patterns

### Pattern 1: Displaying Plan List

```typescript
const SubscriptionPlans = () => {
  const plans = useSelector(selectSubscriptionPlans)

  // Sort and filter active plans
  const activePlans = sortPlans(plans.filter(p => p.status))

  return (
    <div>
      {activePlans.map(plan => (
        <PlanCard
          key={plan.id}
          plan={plan}
          isHighlighted={plan.is_featured}
          priceDisplay={getPriceDisplay(plan)}
          storageDisplay={getStorageDisplayText(plan)}
        />
      ))}
    </div>
  )
}
```

### Pattern 2: Checking User's Plan Tier

```typescript
const currentPlan = plans.find(p => p.id === userSubscription.plan_id)

if (currentPlan) {
  if (isFreePlan(currentPlan)) {
    // Free tier logic
    showUpgradePrompt()
  } else if (isPremiumTierPlan(currentPlan)) {
    // Premium+ tier logic
    enablePremiumFeatures()
  }
}
```

### Pattern 3: Upgrade/Downgrade Flow

```typescript
const handleSelectPlan = (selectedPlan: SubscriptionPlan) => {
  const currentPlan = getCurrentUserPlan()

  if (!currentPlan) {
    // New user, any selection is an "upgrade"
    proceedToCheckout(selectedPlan)
  } else if (isUpgrade(selectedPlan, currentPlan)) {
    // Upgrading
    proceedToCheckout(selectedPlan)
  } else if (isDowngrade(selectedPlan, currentPlan)) {
    // Downgrading - show confirmation
    showDowngradeConfirmation(selectedPlan)
  } else {
    // Same tier
    showMessage("You're already on this plan")
  }
}
```

## Debugging Tips

### 1. Check Plan Data Structure

```typescript
console.log('Plan data:', {
  id: plan.id,
  name: plan.name,
  tier: plan.tier,
  price: plan.price,
  display_order: plan.display_order,
  is_featured: plan.is_featured,
})
```

### 2. Verify Tier Detection

```typescript
console.log('Tier checks:', {
  isFree: isFreePlan(plan),
  isPremium: isPremiumTierPlan(plan),
  tierName: getPlanTierDisplayName(plan),
})
```

### 3. Test Sorting

```typescript
console.log('Plan order:', sortPlans(plans).map(p => ({
  name: p.name,
  order: p.display_order,
  price: p.price
})))
```

## Summary

The frontend now supports:

✅ **Unlimited plans** - Add as many as you want
✅ **Dynamic sorting** - Based on `display_order` or price
✅ **Tier-based logic** - No hardcoded checks
✅ **Featured plans** - Highlight important plans
✅ **Flexible storage** - Show any quota or "Unlimited"
✅ **Backwards compatible** - Works with old and new plans
✅ **Automatic adaptation** - No code changes needed for new plans

When the backend adds a new plan, the frontend automatically:
- Displays it in the correct order
- Shows the right pricing and features
- Handles checkout flows correctly
- Applies proper tier-based logic

**No frontend code changes required!** 🎉
