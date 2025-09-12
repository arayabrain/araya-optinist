import { SubscriptionPlanDTO } from "api/subscriptions/SubscriptionsApiDTO"
import axios from "utils/axios"

export const getSubscriptionPlansApi = async (): Promise<
  SubscriptionPlanDTO[]
> => {
  const response = await axios.get("/api/subscriptions/plans")
  return response.data
}

export const getUserSubscriptionApi = async (user_id: number) => {
  const response = await axios.get(`/api/subscriptions/user/${user_id}`)
  return response.data
}

export const createCheckoutSessionApi = async (planId: number) => {
  const response = await axios.post(
    "/api/subscriptions/create-checkout-session",
    {
      plan_id: planId,
    },
  )
  return response.data
}
