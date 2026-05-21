import { createSelector } from "@reduxjs/toolkit"

import { RootState } from "store/store"

// Base selector
const selectPaymentMethodsState = (state: RootState) => state.paymentMethod

// Default Payment Method selectors
export const selectDefaultPaymentMethod = createSelector(
  [selectPaymentMethodsState],
  (state) => state.defaultPaymentMethod,
)

export const selectDefaultPaymentMethodLoading = createSelector(
  [selectPaymentMethodsState],
  (state) => state.loading.defaultPaymentMethod,
)

export const selectDefaultPaymentMethodError = createSelector(
  [selectPaymentMethodsState],
  (state) => state.error.defaultPaymentMethod,
)

// All Payment Methods selectors
export const selectAllPaymentMethods = createSelector(
  [selectPaymentMethodsState],
  (state) => state.allPaymentMethods,
)

export const selectAllPaymentMethodsLoading = createSelector(
  [selectPaymentMethodsState],
  (state) => state.loading.allPaymentMethods,
)

export const selectAllPaymentMethodsError = createSelector(
  [selectPaymentMethodsState],
  (state) => state.error.allPaymentMethods,
)

// Invoices selectors
export const selectInvoices = createSelector(
  [selectPaymentMethodsState],
  (state) => state.invoices,
)

export const selectInvoicesLoading = createSelector(
  [selectPaymentMethodsState],
  (state) => state.loading.invoices,
)

export const selectInvoicesError = createSelector(
  [selectPaymentMethodsState],
  (state) => state.error.invoices,
)

// Combined loading state
export const selectPaymentMethodsAnyLoading = createSelector(
  [selectPaymentMethodsState],
  (state) =>
    state.loading.defaultPaymentMethod ||
    state.loading.allPaymentMethods ||
    state.loading.invoices,
)

// Combined error state
export const selectPaymentMethodsHasError = createSelector(
  [selectPaymentMethodsState],
  (state) =>
    state.error.defaultPaymentMethod !== null ||
    state.error.allPaymentMethods !== null ||
    state.error.invoices !== null,
)

// Get first error message
export const selectFirstPaymentMethodsError = createSelector(
  [selectPaymentMethodsState],
  (state) =>
    state.error.defaultPaymentMethod ||
    state.error.allPaymentMethods ||
    state.error.invoices,
)

// Check if user has any payment methods
export const selectHasPaymentMethods = createSelector(
  [selectAllPaymentMethods],
  (paymentMethods) => paymentMethods.length > 0,
)

// Get active payment methods only
export const selectActivePaymentMethods = createSelector(
  [selectAllPaymentMethods],
  (paymentMethods) =>
    paymentMethods.filter((pm) => pm.is_default || paymentMethods.length === 1),
)
