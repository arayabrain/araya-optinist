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

export type CheckoutValidationStatus =
  | "success"
  | "payment_failed"
  | "webhook_failed"

export type CheckoutValidationResponse = {
  status: CheckoutValidationStatus
  message?: string
}
