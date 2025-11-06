export type SubscriptionPlanDTO = {
  id: number
  name: string
  price: number
  billing_cycle: string
  features: string[]
  currency: string
  status: boolean
  created_at: string
}
