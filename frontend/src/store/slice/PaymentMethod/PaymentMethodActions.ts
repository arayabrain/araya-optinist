import { createAsyncThunk } from "@reduxjs/toolkit"

import {
  getDefaultPaymentMethodApi,
  getAllPaymentMethodsApi,
  getInvoicesApi,
} from "api/paymentMethod/PaymentMethod"
import {
  PaymentMethodDTO,
  InvoiceDTO,
} from "api/paymentMethod/PaymentMethodApiDTO"

// Helper function to extract error message
const extractErrorMessage = (error: unknown): string => {
  if (typeof error === "string") {
    return error
  }

  if (error && typeof error === "object") {
    const errorObj = error as Record<string, unknown>

    // Check for Axios error structure
    if (errorObj.response && typeof errorObj.response === "object") {
      const response = errorObj.response as Record<string, unknown>
      if (response.data && typeof response.data === "object") {
        const data = response.data as Record<string, unknown>
        if (typeof data.detail === "string") {
          return data.detail
        }
        if (typeof data.message === "string") {
          return data.message
        }
      }
    }
    if (typeof errorObj.message === "string") {
      return errorObj.message
    }
  }

  return "An unexpected error occurred"
}

export const PAYMENT_METHODS_SLICE_NAME = "paymentMethods"

export const getDefaultPaymentMethod = createAsyncThunk<
  PaymentMethodDTO | null,
  number | undefined,
  { rejectValue: string }
>(
  `${PAYMENT_METHODS_SLICE_NAME}/getDefaultPaymentMethod`,
  async (_, thunkAPI) => {
    try {
      const response = await getDefaultPaymentMethodApi()
      return response
    } catch (error) {
      // eslint-disable-next-line no-console
      console.error("Error fetching default payment method:", error)

      // Handle 404 specifically (no payment method found)
      if (error && typeof error === "object") {
        const errorObj = error as Record<string, unknown>
        if (errorObj.response && typeof errorObj.response === "object") {
          const response = errorObj.response as Record<string, unknown>
          if (response.status === 404) {
            return null // No payment method found is not an error
          }
        }
      }

      const errorMessage = extractErrorMessage(error)
      return thunkAPI.rejectWithValue(errorMessage)
    }
  },
)

export const getAllPaymentMethods = createAsyncThunk<
  PaymentMethodDTO[],
  number,
  { rejectValue: string }
>(`${PAYMENT_METHODS_SLICE_NAME}/getAllPaymentMethods`, async (_, thunkAPI) => {
  try {
    const response = await getAllPaymentMethodsApi()

    // Validate response structure
    if (!Array.isArray(response)) {
      // eslint-disable-next-line no-console
      console.warn("Invalid payment methods response:", response)
      return []
    }

    return response
  } catch (error) {
    // eslint-disable-next-line no-console
    console.error("Error fetching payment methods:", error)
    const errorMessage = extractErrorMessage(error)
    return thunkAPI.rejectWithValue(errorMessage)
  }
})

export const getUserInvoices = createAsyncThunk<
  InvoiceDTO[],
  number | undefined,
  { rejectValue: string }
>(
  `${PAYMENT_METHODS_SLICE_NAME}/getUserInvoices`,
  async (userId: number | undefined, thunkAPI) => {
    try {
      const response = await getInvoicesApi(userId)

      // Validate response structure
      if (!Array.isArray(response)) {
        // eslint-disable-next-line no-console
        console.warn("Invalid invoices response:", response)
        return []
      }

      return response
    } catch (error) {
      // eslint-disable-next-line no-console
      console.error("Error fetching invoices:", error)
      const errorMessage = extractErrorMessage(error)
      return thunkAPI.rejectWithValue(errorMessage)
    }
  },
)
