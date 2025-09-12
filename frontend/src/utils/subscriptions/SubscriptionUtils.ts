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
    console.warn("Failed to parse features:", error)
    return {}
  }
}

// Helper function to safely convert plan data
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
    }
  } catch (error) {
    console.warn("Failed to convert plan data:", error)
    return {
      id: 0,
      name: "Unknown Plan",
      price: 0,
      billing_cycle: BillingCycle.MONTHLY,
      currency: Currency.USD,
      features: {},
      status: false,
      created_at: "",
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
      console.warn(`No features found for plan ${plan.id}:`, plan.features)
      return []
    }

    // Extract features based on plan name
    const planName = plan.name
    const featuresData = plan.features[planName]

    if (!Array.isArray(featuresData)) {
      console.warn(`Invalid features data for plan ${planName}:`, featuresData)
      return []
    }

    return featuresData
  } catch (error) {
    console.error(`Error extracting features for plan ${plan.id}:`, error)
    return []
  }
}

export const getAccurateTime = async () => {
  try {
    const response = await fetch("http://worldtimeapi.org/api/timezone/Etc/UTC")
    const data = await response.json()
    return new Date(data.datetime)
  } catch (error) {
    // Fallback to client time if API fails
    return new Date()
  }
}
