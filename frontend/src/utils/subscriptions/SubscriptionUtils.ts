// store/slice/Subscriptions/SubscriptionUtils.ts
import {
  PlanFeature,
  SubscriptionPlan,
} from "store/slice/Subscriptions/SubscriptionType"

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
    // eslint-disable-next-line no-console
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
      billing_cycle: Number(planData.billing_cycle) || 1,
      currency: Number(planData.currency) || 1,
      features: safeParseFeatures(planData.features),
      status: Boolean(planData.status),
      created_at: String(planData.created_at || ""),
    }
  } catch (error) {
    // eslint-disable-next-line no-console
    console.warn("Failed to convert plan data:", error)
    return {
      id: 0,
      name: "Unknown Plan",
      price: 0,
      billing_cycle: 1,
      currency: 1,
      features: {},
      status: false,
      created_at: "",
    }
  }
}

// Component utility functions
export const getBillingCycleText = (billingCycle: number): string => {
  switch (billingCycle) {
    case 1:
      return "month"
    case 2:
      return "year"
    default:
      return "month"
  }
}

export const getCurrencySymbol = (currency: number): string => {
  switch (currency) {
    case 1:
      return "$"
    case 2:
      return "¥"
    default:
      return "$"
  }
}

export const formatPrice = (
  priceInCents: number,
  currency: number = 1,
): string => {
  const symbol = getCurrencySymbol(currency)
  return `${symbol}${(priceInCents / 100).toFixed(2)}`
}

// Feature extraction utility
export const getPlanFeatures = (plan: SubscriptionPlan): PlanFeature[] => {
  try {
    if (!plan.features || typeof plan.features !== "object") {
      // eslint-disable-next-line no-console
      console.warn(`No features found for plan ${plan.id}:`, plan.features)
      return []
    }

    // Extract features based on plan name
    const planName = plan.name
    const featuresData = plan.features[planName]

    if (!Array.isArray(featuresData)) {
      // eslint-disable-next-line no-console
      console.warn(`Invalid features data for plan ${planName}:`, featuresData)
      return []
    }

    return featuresData
  } catch (error) {
    // eslint-disable-next-line no-console
    console.error(`Error extracting features for plan ${plan.id}:`, error)
    return []
  }
}
