export type SubscriptionPlanDTO = {
  id: number
  name: string
  price: number
  billing_cycle: number | string
  features: Record<string, unknown> | string[]
  currency: number | string
  status: boolean
  created_at: string
  tier?: string
  display_order?: number
  is_featured?: boolean
  stripe_product_id?: string
  stripe_price_id?: string
}
