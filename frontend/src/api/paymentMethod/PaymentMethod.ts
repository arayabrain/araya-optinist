import {
  PaymentMethodDTO,
  InvoiceDTO,
} from "api/paymentMethod/PaymentMethodApiDTO"
import axios from "utils/axios"

export const getDefaultPaymentMethodApi = async (
  userId: number | undefined,
): Promise<PaymentMethodDTO | null> => {
  const response = await axios.get(
    `/subscriptions/payment-methods/${userId}/default`,
  )
  return response.data
}

export const getAllPaymentMethodsApi = async (
  userId: number | undefined,
): Promise<PaymentMethodDTO[]> => {
  const response = await axios.get(`/subscriptions/payment-methods/${userId}`)
  return response.data
}

export const getInvoicesApi = async (
  userId: number | undefined,
): Promise<InvoiceDTO[]> => {
  const response = await axios.get(`/subscriptions/invoices/${userId}`)
  return response.data
}
