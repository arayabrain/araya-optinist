import { createSlice, PayloadAction } from "@reduxjs/toolkit"

import { SUBSCRIPTION_USER_STATUS } from "pages/Account"
import {
  cancelSubscription,
  createCheckoutSession,
  getSubscriptionPlan,
  getUserSubscription,
  validateCheckoutSession,
} from "store/slice/Subscriptions/SubscriptionActions"
import {
  SUBSCRIPTION_SLICE_NAME,
  SubscriptionState,
  UserSubscription,
} from "store/slice/Subscriptions/SubscriptionType"
import {
  extractRejectedErrorMessage,
  safeConvertPlan,
} from "utils/subscriptions/SubscriptionUtils"

const initialState: SubscriptionState = {
  plans: [],
  userSubscription: null,
  loading: false,
  checkoutLoading: false,
  error: null,
  plansLoading: false,
  userSubscriptionLoading: false,
}

const subscriptionSlice = createSlice({
  name: SUBSCRIPTION_SLICE_NAME,
  initialState,
  reducers: {
    clearError: (state) => {
      state.error = null
    },
    clearUserSubscription: (state) => {
      state.userSubscription = null
    },
    resetSubscriptionState: (state) => {
      state.plans = []
      state.userSubscription = null
      state.loading = false
      state.error = null
      state.plansLoading = false
      state.userSubscriptionLoading = false
    },
  },
  extraReducers: (builder) => {
    builder
      // Subscription Plans
      .addCase(getSubscriptionPlan.pending, (state) => {
        state.plansLoading = true
        state.loading = true
        state.error = null
      })
      .addCase(getSubscriptionPlan.fulfilled, (state, action) => {
        try {
          state.plansLoading = false
          state.loading = false
          state.error = null

          // Safely convert the payload
          if (Array.isArray(action.payload)) {
            state.plans = action.payload.map((planData: unknown) =>
              safeConvertPlan(planData as Record<string, unknown>),
            )
          } else {
            state.plans = []
          }
        } catch (error) {
          state.plans = []
          state.error = "Failed to process subscription plans data"
        }
      })
      .addCase(getSubscriptionPlan.rejected, (state, action) => {
        state.plansLoading = false
        state.loading = false
        state.plans = []
        state.error = extractRejectedErrorMessage(
          action,
          "Failed to load subscription plans",
        )
      })

    builder
      // User Subscription
      .addCase(getUserSubscription.pending, (state) => {
        state.userSubscriptionLoading = true
        state.loading = true
        state.error = null
      })
      .addCase(
        getUserSubscription.fulfilled,
        (state, action: PayloadAction<UserSubscription>) => {
          try {
            state.userSubscriptionLoading = false
            state.loading = false
            state.error = null

            if (action.payload && typeof action.payload === "object") {
              state.userSubscription = {
                id: Number(action.payload.id) || 0,
                plan_id: Number(action.payload.plan_id) || 0,
                user_id: Number(action.payload.user_id) || 0,
                expiration: String(action.payload.expiration || ""),
                status:
                  Number(action.payload.status) ||
                  SUBSCRIPTION_USER_STATUS.FREE,
                scheduled_downgrade: Boolean(
                  action.payload.scheduled_downgrade,
                ),
                plan_name: String(action.payload.plan_name || ""),
                plan_price: Number(action.payload.plan_price) || 0,
              }
            } else {
              state.userSubscription = null
            }
          } catch (error) {
            state.userSubscription = null
            state.error = "Failed to process user subscription data"
          }
        },
      )
      .addCase(getUserSubscription.rejected, (state, action) => {
        state.userSubscriptionLoading = false
        state.loading = false
        state.userSubscription = null
        state.error = extractRejectedErrorMessage(
          action,
          "Failed to load user subscription",
        )
      })
      .addCase(createCheckoutSession.pending, (state) => {
        state.checkoutLoading = true
        state.error = null
      })
      .addCase(createCheckoutSession.fulfilled, (state) => {
        state.checkoutLoading = false
      })
      .addCase(createCheckoutSession.rejected, (state, action) => {
        state.checkoutLoading = false
        state.error = action.payload || "Failed to create checkout session"
      })
      .addCase(cancelSubscription.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(cancelSubscription.fulfilled, (state) => {
        state.loading = false
        if (state.userSubscription) {
          state.userSubscription.scheduled_downgrade = true
        }
      })
      .addCase(cancelSubscription.rejected, (state, action) => {
        state.loading = false
        state.error = extractRejectedErrorMessage(
          action,
          "Failed to cancel subscription",
        )
      })
      .addCase(validateCheckoutSession.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(validateCheckoutSession.fulfilled, (state) => {
        state.loading = false
        state.error = null
        // Handle successful validation if needed
      })
      .addCase(validateCheckoutSession.rejected, (state, action) => {
        state.loading = false
        state.error = extractRejectedErrorMessage(
          action,
          "Payment validation failed",
        )
        // Show alert for payment failure
        alert("Payment failed. Please try again or contact support.")
      })
  },
})

export const { clearError, clearUserSubscription, resetSubscriptionState } =
  subscriptionSlice.actions

export default subscriptionSlice.reducer
