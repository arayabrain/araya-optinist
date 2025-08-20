import { SubscriptionPlanDTO } from "api/subscriptions/SubscriptionsApiDTO"
import axios from "utils/axios"

export const getSubscriptionPlansApi = async (): Promise<
  SubscriptionPlanDTO[]
> => {
  const response = await axios.get("/subscriptions/plans")
  // eslint-disable-next-line no-console
  console.log("Subscription plans response:", response.data)
  return response.data
}

export const getUserSubscriptionApi = async (user_id: number) => {
  const response = await axios.get(`/subscriptions/user/${user_id}`)
  // eslint-disable-next-line no-console
  console.log("User subscription response:", response.data)
  return response.data
}
