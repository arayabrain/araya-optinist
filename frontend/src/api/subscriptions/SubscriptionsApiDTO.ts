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

export const CheckoutValidationStatus = {
  SUCCESS: "success",
  PAYMENT_FAILED: "payment_failed",
  WEBHOOK_FAILED: "webhook_failed",
} as const

export type CheckoutValidationStatus =
  (typeof CheckoutValidationStatus)[keyof typeof CheckoutValidationStatus]

export type CheckoutValidationResponse = {
  status: CheckoutValidationStatus
  message?: string
}
