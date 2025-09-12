// store/slice/Subscriptions/SubscriptionActions.ts
import _ from "lodash"

import { createAsyncThunk } from "@reduxjs/toolkit"

import {
  createCheckoutSessionApi,
  getSubscriptionPlansApi,
  getUserSubscriptionApi,
} from "api/subscriptions/Subscriptions"
import {
  CreateCheckoutSessionResponse,
  SUBSCRIPTION_SLICE_NAME,
} from "store/slice/Subscriptions/SubscriptionType"

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

export const getSubscriptionPlan = createAsyncThunk(
  `${SUBSCRIPTION_SLICE_NAME}/getSubscriptionPlan`,
  async (_, thunkAPI) => {
    try {
      const response = await getSubscriptionPlansApi()

      // Validate response structure
      if (!Array.isArray(response)) {
        console.warn("Invalid subscription plans response:", response)
        return []
      }

      return response
    } catch (error) {
      console.error("Error fetching subscription plans:", error)
      // Extract clean error message instead of passing entire error object
      const errorMessage = extractErrorMessage(error)
      return thunkAPI.rejectWithValue(errorMessage)
    }
  },
)

export const getUserSubscription = createAsyncThunk(
  `${SUBSCRIPTION_SLICE_NAME}/getUserSubscription`,
  async (userId: number, thunkAPI) => {
    try {
      const response = await getUserSubscriptionApi(userId)
      return response
    } catch (error) {
      console.error("Error fetching user subscription:", error)
      // Extract clean error message instead of passing entire error object
      const errorMessage = extractErrorMessage(error)
      return thunkAPI.rejectWithValue(errorMessage)
    }
  },
)

export const createCheckoutSession = createAsyncThunk<
  CreateCheckoutSessionResponse,
  number,
  { rejectValue: string }
>("subscription/createCheckoutSession", async (planId, { rejectWithValue }) => {
  try {
    const response = await createCheckoutSessionApi(planId)
    return response
  } catch (error: unknown) {
    const message = extractErrorMessage(error)
    return rejectWithValue(message)
  }
})
