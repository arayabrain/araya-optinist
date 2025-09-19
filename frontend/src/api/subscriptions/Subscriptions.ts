import { createAsyncThunk } from "@reduxjs/toolkit"

import { SubscriptionPlanDTO } from "api/subscriptions/SubscriptionsApiDTO"
import axios from "utils/axios"

export const getSubscriptionPlansApi = async (): Promise<
  SubscriptionPlanDTO[]
> => {
  const response = await axios.get("/api/subsc/mgmts/plans")
  return response.data
}

export const getUserSubscriptionApi = async (user_id: number) => {
  const response = await axios.get(`/api/subsc/mgmts/${user_id}`)
  return response.data
}

export const createCheckoutSessionApi = async (planId: number) => {
  const response = await axios.post(
    "/api/subsc/checkout/create-checkout-session",
    {
      plan_id: planId,
    },
  )
  return response.data
}

export const validateCheckoutSessionApi = createAsyncThunk(
  "subscription/validateCheckoutSession",
  async (sessionId: string) => {
    const response = await axios.post(
      "/api/subsc/checkout/validate-checkout-session",
      {
        session_id: sessionId,
      },
    )
    return response.data
  },
)

export const validateFailedCheckoutSessionApi = async (sessionId: string) => {
  const response = await axios.post(
    "/api/subsc/checkout/validate-failed-checkout-session",
    {
      session_id: sessionId,
    },
  )
  return response.data
}
