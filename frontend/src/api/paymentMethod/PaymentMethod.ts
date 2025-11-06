import {
  PaymentMethodDTO,
  InvoiceDTO,
} from "api/paymentMethod/PaymentMethodApiDTO"
import axios from "utils/axios"

export const getDefaultPaymentMethodApi =
  async (): Promise<PaymentMethodDTO | null> => {
    const response = await axios.get("/api/subsc/payment-methods/default")
    return response.data
  }

export const getAllPaymentMethodsApi = async (): Promise<
  PaymentMethodDTO[]
> => {
  const response = await axios.get("/api/subsc/payment-methods")
  return response.data
}

export const getInvoicesApi = async (
  _userId: number | undefined,
): Promise<InvoiceDTO[]> => {
  const response = await axios.get(`/api/subsc/invoices/${_userId}`)
  return response.data
}
