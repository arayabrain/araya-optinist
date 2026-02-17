export interface PaymentMethodDTO {
  id: string
  type: "card" | "link"
  last4?: string
  brand?: string
  exp_month?: number
  exp_year?: number
  is_default: boolean
  email?: string
}

export interface InvoiceDTO {
  id: string
  date: string
  total: string
  status: string
  invoice_url: string
}
