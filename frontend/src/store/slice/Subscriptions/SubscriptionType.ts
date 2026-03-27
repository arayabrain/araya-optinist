export const SUBSCRIPTION_SLICE_NAME = "subscription"

export interface PlanFeature {
  text: string
  isPremium: boolean
}

export interface SubscriptionPlan {
  id: number
  name: string
  price: number
  billing_cycle: number
  features: Record<string, PlanFeature[]>
  currency: number
  status: boolean
  created_at: string
  tier?: string
  display_order?: number
  is_featured?: boolean
  is_hidden?: boolean
  stripe_product_id?: string
  stripe_price_id?: string
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
  deletionPriority: string | null
  deletionPriorityLoading: boolean
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
