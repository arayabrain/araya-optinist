import { createSlice, PayloadAction } from "@reduxjs/toolkit"

import {
  getSubscriptionPlan,
  getUserSubscription,
} from "store/slice/Subscriptions/SubscriptionActions"
import { SUBSCRIPTION_SLICE_NAME } from "store/slice/Subscriptions/SubscriptionType"
interface SubscriptionPlan {
  id: number
  name: string
  price: number
  created_at: string
}

interface UserSubscription {
  id: number
  plan_id: number
  user_id: number
  expiration: string
  plan_name: string
  plan_price: number
}

interface SubscriptionState {
  plans: SubscriptionPlan[]
  userSubscription: UserSubscription | null
  loading: boolean
  error: string | null
  plansLoading: boolean
  userSubscriptionLoading: boolean
}

const initialState: SubscriptionState = {
  plans: [],
  userSubscription: null,
  loading: false,
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
  },
  extraReducers: (builder) => {
    builder
      .addCase(getSubscriptionPlan.pending, (state) => {
        state.plansLoading = true
        state.loading = true
        state.error = null
      })
      .addCase(
        getSubscriptionPlan.fulfilled,
        (state, action: PayloadAction<SubscriptionPlan[]>) => {
          state.plansLoading = false
          state.loading = false
          state.plans = action.payload
          state.error = null
        },
      )
      .addCase(getSubscriptionPlan.rejected, (state, action) => {
        state.plansLoading = false
        state.loading = false
        state.error =
          (action.payload as string) || "Failed to load subscription plans"
      })

    builder
      .addCase(getUserSubscription.pending, (state) => {
        state.userSubscriptionLoading = true
        state.loading = true
        state.error = null
      })
      .addCase(
        getUserSubscription.fulfilled,
        (state, action: PayloadAction<UserSubscription>) => {
          state.userSubscriptionLoading = false
          state.loading = false
          state.userSubscription = action.payload
          state.error = null
        },
      )
      .addCase(getUserSubscription.rejected, (state, action) => {
        state.userSubscriptionLoading = false
        state.loading = false
        state.error =
          (action.payload as string) || "Failed to load user subscription"
      })
  },
})

export const { clearError, clearUserSubscription } = subscriptionSlice.actions
export default subscriptionSlice.reducer
