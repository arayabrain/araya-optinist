import { SubscriptionPlanDTO } from "api/subscriptions/SubscriptionsApiDTO"
import axios from "utils/axios"

export const getSubscriptionPlansApi = async (): Promise<
  SubscriptionPlanDTO[]
> => {
  const response = await axios.get("/subscriptions/plans")
  console.log("Subscription plans response:", response.data)
  return response.data
}

export const getUserSubscriptionApi = async (user_id: number) => {
  const response = await axios.get(`/subscriptions/user/${user_id}`)
  console.log("User subscription response:", response.data)
  return response.data
}

export const createCheckoutSessionApi = async (planId: number) => {
  const response = await axios.post("/subscriptions/create-checkout-session", {
    plan_id: planId,
  })
  console.log("API Response:", response)
  console.log("API Response Data:", response.data)
  return response.data
}
