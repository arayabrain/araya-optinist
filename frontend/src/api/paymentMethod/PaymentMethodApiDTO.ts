export interface PaymentMethodDTO {
  id: string
  last4: string
  brand: string
  exp_month: number
  exp_year: number
  is_default: boolean
}

export interface InvoiceDTO {
  id: string
  date: string
  total: string
  status: string
  invoice_url: string
}
