import {
  PlanFeature,
  SubscriptionPlan,
} from "store/slice/Subscriptions/SubscriptionType"

// Enums for billing cycles
export enum BillingCycle {
  MONTHLY = 1,
  YEARLY = 2,
}

// Enums for currency types
export enum Currency {
  USD = 1,
  JPY = 2,
}

// Enum for billing cycle text mapping
export enum BillingCycleText {
  MONTHLY = "month",
  YEARLY = "year",
}

// Enum for currency symbols
export enum CurrencySymbol {
  USD = "$",
  JPY = "¥",
}

// Helper function to safely extract error message from rejected actions
export const extractRejectedErrorMessage = (
  action: unknown,
  fallbackMessage: string,
): string => {
  try {
    const actionObj = action as Record<string, unknown>

    // action.payload should now be a string from our fixed thunks
    if (typeof actionObj.payload === "string") {
      return actionObj.payload
    }

    // Fallback to action.error.message if payload isn't a string
    if (actionObj.error && typeof actionObj.error === "object") {
      const error = actionObj.error as Record<string, unknown>
      if (typeof error.message === "string") {
        return error.message
      }
    }

    return fallbackMessage
  } catch {
    return fallbackMessage
  }
}

// Helper function to safely parse features
export const safeParseFeatures = (
  features: unknown,
): Record<string, PlanFeature[]> => {
  try {
    // If it's already a proper object, return it
    if (features && typeof features === "object" && !Array.isArray(features)) {
      return features as Record<string, PlanFeature[]>
    }

    // If it's a JSON string, parse it
    if (typeof features === "string") {
      const parsed = JSON.parse(features) as unknown
      if (parsed && typeof parsed === "object") {
        return parsed as Record<string, PlanFeature[]>
      }
    }

    // Fallback to empty object
    return {}
  } catch (error) {
    return {}
  }
}

// Helper function to safely convert plan data
// Includes tier-based fields for multi-plan support
export const safeConvertPlan = (
  planData: Record<string, unknown>,
): SubscriptionPlan => {
  try {
    return {
      id: Number(planData.id) || 0,
      name: String(planData.name || ""),
      price: Number(planData.price) || 0,
      billing_cycle: Number(planData.billing_cycle) || BillingCycle.MONTHLY,
      currency: Number(planData.currency) || Currency.USD,
      features: safeParseFeatures(planData.features),
      status: Boolean(planData.status),
      created_at: String(planData.created_at || ""),
      // Plan management fields
      tier: planData.tier ? String(planData.tier) : undefined,
      display_order: planData.display_order
        ? Number(planData.display_order)
        : undefined,
      is_featured:
        planData.is_featured !== undefined
          ? Boolean(planData.is_featured)
          : undefined,
      stripe_product_id: planData.stripe_product_id
        ? String(planData.stripe_product_id)
        : undefined,
      stripe_price_id: planData.stripe_price_id
        ? String(planData.stripe_price_id)
        : undefined,
    }
  } catch (error) {
    return {
      id: 0,
      name: "Unknown Plan",
      price: 0,
      billing_cycle: BillingCycle.MONTHLY,
      currency: Currency.USD,
      features: {},
      status: false,
      created_at: "",
      tier: "free",
    }
  }
}

// Component utility functions
export const getBillingCycleText = (billingCycle: number): string => {
  switch (billingCycle) {
    case BillingCycle.MONTHLY:
      return BillingCycleText.MONTHLY
    case BillingCycle.YEARLY:
      return BillingCycleText.YEARLY
    default:
      return BillingCycleText.MONTHLY
  }
}

export const getCurrencySymbol = (currency: number): string => {
  switch (currency) {
    case Currency.USD:
      return CurrencySymbol.USD
    case Currency.JPY:
      return CurrencySymbol.JPY
    default:
      return CurrencySymbol.USD
  }
}

export const formatPrice = (
  priceInCents: number,
  currency: number = Currency.USD,
): string => {
  const symbol = getCurrencySymbol(currency)
  return `${symbol}${(priceInCents / 100).toFixed(2)}`
}

// Feature extraction utility
export const getPlanFeatures = (plan: SubscriptionPlan): PlanFeature[] => {
  try {
    if (!plan.features || typeof plan.features !== "object") {
      return []
    }

    // Extract features based on plan name
    const planName = plan.name
    const featuresData = plan.features[planName]

    if (!Array.isArray(featuresData)) {
      return []
    }

    return featuresData
  } catch (error) {
    return []
  }
}

export const getAccurateTimeUTC = async () => {
  try {
    const response = await fetch("http://worldtimeapi.org/api/timezone/UTC")
    const data = await response.json()
    return new Date(data.utc_datetime)
  } catch (error) {
    return new Date() // JavaScript Date is UTC internally
  }
}

// ============================================================================
// Tier-Based Helper Functions (Multi-Plan Support)
// ============================================================================

/**
 * Check if a plan is a free tier plan
 * Uses tier field if available, falls back to price check
 */
export const isFreePlan = (plan: SubscriptionPlan): boolean => {
  if (plan.tier) {
    return plan.tier === "free"
  }
  // Fallback to price-based check
  return plan.price === 0
}

/**
 * Check if a plan is premium tier or higher
 * Includes: premium, enterprise, professional, and any custom premium tiers
 */
export const isPremiumTierPlan = (plan: SubscriptionPlan): boolean => {
  if (plan.tier) {
    const premiumTiers = ["premium", "enterprise", "professional", "team"]
    return premiumTiers.includes(plan.tier.toLowerCase())
  }
  // Fallback to price-based check
  return plan.price > 0
}

/**
 * Get plan tier display name
 * Returns a user-friendly tier name
 */
export const getPlanTierDisplayName = (plan: SubscriptionPlan): string => {
  if (!plan.tier) {
    return plan.price === 0 ? "Free" : "Premium"
  }

  // Capitalize first letter
  return plan.tier.charAt(0).toUpperCase() + plan.tier.slice(1)
}

/**
 * Check if plan A is an upgrade from plan B
 * Based on price comparison (higher price = upgrade)
 */
export const isUpgrade = (
  planA: SubscriptionPlan,
  planB: SubscriptionPlan,
): boolean => {
  return planA.price > planB.price
}

/**
 * Check if plan A is a downgrade from plan B
 * Based on price comparison (lower price = downgrade)
 */
export const isDowngrade = (
  planA: SubscriptionPlan,
  planB: SubscriptionPlan,
): boolean => {
  return planA.price < planB.price
}

/**
 * Sort plans by display order or price
 * Returns a new sorted array
 */
export const sortPlans = (plans: SubscriptionPlan[]): SubscriptionPlan[] => {
  return [...plans].sort((a, b) => {
    // First sort by display_order if available
    if (a.display_order !== undefined && b.display_order !== undefined) {
      return a.display_order - b.display_order
    }
    // Fallback to price sorting (free first, then by ascending price)
    if (a.price === 0 && b.price > 0) return -1
    if (a.price > 0 && b.price === 0) return 1
    return a.price - b.price
  })
}
