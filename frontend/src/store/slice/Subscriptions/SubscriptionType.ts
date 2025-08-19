export const SUBSCRIPTION_SLICE_NAME = "subscription"

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
  plan_name: string
  plan_price: number
}

export interface SubscriptionState {
  plans: SubscriptionPlan[]
  userSubscription: UserSubscription | null
  loading: boolean
  error: string | null
  plansLoading: boolean
  userSubscriptionLoading: boolean
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
