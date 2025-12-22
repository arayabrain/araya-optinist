export const SUBSCRIPTION_SLICE_NAME = "subscription"

// Subscription plan names (matches backend PlanName enum)
export enum SUBSCRIPTION_PLAN {
  FREE = "Free",
  PREMIUM = "Premium",
}

// User tier identifiers for API/routing (matches backend SubscriptionType enum)
export enum USER_TIER {
  PREMIUM = "premium",
  FREE = "free",
}

// Subscription user status (matches backend SubscriptionUserStatus enum)
export enum SUBSCRIPTION_USER_STATUS {
  FREE = 1,
  SUBSCRIBED = 2,
  EXPIRED = 3,
  CANCELED = 4,
}

// Subscription status labels (matches backend SubscriptionStatus enum)
export enum SUBSCRIPTION_STATUS {
  FREE = "Free",
  PREMIUM = "Premium",
  LIMIT_GRACE = "Limit Grace",
  EXPIRED = "Expired",
}

// Limit alert types (used in storage and subscription warnings)
export enum LIMIT_ALERT_TYPE {
  STORAGE = "storage",
  GRACE = "grace",
  OVERDUE = "overdue",
}

// Premium instance timing constants (in minutes)
export const PREMIUM_TIMING = {
  // Duration of inactivity warning countdown before instance is released
  INACTIVITY_WARNING_DURATION_MINUTES: 60,
  // Interval for updating the countdown display (in milliseconds)
  WARNING_UPDATE_INTERVAL_MS: 60 * 1000, // 1 minute
} as const

// Feature interface for type safety
export interface PlanFeature {
  text: string
  isPremium: boolean
}

// Updated SubscriptionPlan interface to match backend response
export interface SubscriptionPlan {
  id: number
  name: string
  price: number
  billing_cycle: number
  features: Record<string, PlanFeature[]>
  currency: number
  status: boolean
  created_at: string
}

export interface UserSubscription {
  id: number
  plan_id: number
  user_id: number
  expiration: string
  is_expired: boolean
  scheduled_downgrade: boolean
  status: number
  plan_name: string
  plan_price: number
}

export interface SubscriptionState {
  plans: SubscriptionPlan[]
  userSubscription: UserSubscription | null
  loading: boolean
  checkoutLoading: boolean
  error: string | null
  plansLoading: boolean
  userSubscriptionLoading: boolean
  serverTime: string | null
}

// API Error types
export interface ApiError {
  message?: string
  detail?: string
}

export interface RejectedAction {
  payload?: string | ApiError
  error?: {
    message?: string
  }
}

export interface CreateCheckoutSessionResponse {
  checkout_url: string
  session_id: string
}
