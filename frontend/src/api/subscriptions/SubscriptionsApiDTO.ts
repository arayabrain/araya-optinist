// Flexible subscription plan DTO that can adapt to any plan configuration
export type SubscriptionPlanDTO = {
  id: number
  name: string
  price: number
  billing_cycle: number | string // Support both numeric enums and string values for future flexibility
  features: Record<string, unknown> | string[] // Support both object structure and array for backward compatibility
  currency: number | string // Support both numeric enums and string currency codes
  status: boolean
  created_at: string
  // Future-proof: These fields can be added without breaking existing code
  tier?: string // Optional tier identifier (e.g., "free", "premium", "enterprise")
  display_order?: number // Optional display order for UI sorting
  is_featured?: boolean // Optional featured flag
  max_storage_gb?: number // Optional storage quota in GB
  stripe_product_id?: string
  stripe_price_id?: string
  description?: string // Optional plan description
  metadata?: Record<string, unknown> // Extensible metadata field for future plan attributes (mapped from plan_metadata in backend)
}
