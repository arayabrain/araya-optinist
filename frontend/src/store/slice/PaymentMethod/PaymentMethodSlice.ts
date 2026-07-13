import { createSlice, PayloadAction } from "@reduxjs/toolkit"

import {
  PaymentMethodDTO,
  InvoiceDTO,
} from "api/paymentMethod/PaymentMethodApiDTO"
import {
  getDefaultPaymentMethod,
  getAllPaymentMethods,
  getUserInvoices,
  PAYMENT_METHODS_SLICE_NAME,
} from "store/slice/PaymentMethod/PaymentMethodActions"

export interface PaymentMethodsState {
  defaultPaymentMethod: PaymentMethodDTO | null
  allPaymentMethods: PaymentMethodDTO[]
  invoices: InvoiceDTO[]
  loading: {
    defaultPaymentMethod: boolean
    allPaymentMethods: boolean
    invoices: boolean
  }
  error: {
    defaultPaymentMethod: string | null
    allPaymentMethods: string | null
    invoices: string | null
  }
}

const initialState: PaymentMethodsState = {
  defaultPaymentMethod: null,
  allPaymentMethods: [],
  invoices: [],
  loading: {
    defaultPaymentMethod: false,
    allPaymentMethods: false,
    invoices: false,
  },
  error: {
    defaultPaymentMethod: null,
    allPaymentMethods: null,
    invoices: null,
  },
}

const paymentMethodsSlice = createSlice({
  name: PAYMENT_METHODS_SLICE_NAME,
  initialState,
  reducers: {
    clearPaymentMethodsError: (state) => {
      state.error.defaultPaymentMethod = null
      state.error.allPaymentMethods = null
      state.error.invoices = null
    },
    clearDefaultPaymentMethodError: (state) => {
      state.error.defaultPaymentMethod = null
    },
    clearAllPaymentMethodsError: (state) => {
      state.error.allPaymentMethods = null
    },
    clearInvoicesError: (state) => {
      state.error.invoices = null
    },
    resetPaymentMethodsState: () => initialState,
  },
  extraReducers: (builder) => {
    // Default Payment Method
    builder
      .addCase(getDefaultPaymentMethod.pending, (state) => {
        state.loading.defaultPaymentMethod = true
        state.error.defaultPaymentMethod = null
      })
      .addCase(
        getDefaultPaymentMethod.fulfilled,
        (state, action: PayloadAction<PaymentMethodDTO | null>) => {
          state.loading.defaultPaymentMethod = false
          state.defaultPaymentMethod = action.payload
          state.error.defaultPaymentMethod = null
        },
      )
      .addCase(getDefaultPaymentMethod.rejected, (state, action) => {
        state.loading.defaultPaymentMethod = false
        state.error.defaultPaymentMethod =
          action.payload || "Failed to fetch default payment method"
      })

    // All Payment Methods
    builder
      .addCase(getAllPaymentMethods.pending, (state) => {
        state.loading.allPaymentMethods = true
        state.error.allPaymentMethods = null
      })
      .addCase(
        getAllPaymentMethods.fulfilled,
        (state, action: PayloadAction<PaymentMethodDTO[]>) => {
          state.loading.allPaymentMethods = false
          state.allPaymentMethods = action.payload
          state.error.allPaymentMethods = null
        },
      )
      .addCase(getAllPaymentMethods.rejected, (state, action) => {
        state.loading.allPaymentMethods = false
        state.error.allPaymentMethods =
          action.payload || "Failed to fetch payment methods"
      })

    // User Invoices
    builder
      .addCase(getUserInvoices.pending, (state) => {
        state.loading.invoices = true
        state.error.invoices = null
      })
      .addCase(
        getUserInvoices.fulfilled,
        (state, action: PayloadAction<InvoiceDTO[]>) => {
          state.loading.invoices = false
          state.invoices = action.payload
          state.error.invoices = null
        },
      )
      .addCase(getUserInvoices.rejected, (state, action) => {
        state.loading.invoices = false
        state.error.invoices = action.payload || "Failed to fetch invoices"
      })
  },
})

export const {
  clearPaymentMethodsError,
  clearDefaultPaymentMethodError,
  clearAllPaymentMethodsError,
  clearInvoicesError,
  resetPaymentMethodsState,
} = paymentMethodsSlice.actions

export default paymentMethodsSlice.reducer
