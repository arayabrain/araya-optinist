import { SubscriptionPlanDTO } from "api/subscriptions/SubscriptionsApiDTO"
import axios from "utils/axios"

export const getSubscriptionPlansApi = async (): Promise<
  SubscriptionPlanDTO[]
> => {
  const response = await axios.get("/subscriptions/plans")
  console.log("getSubscriptionPlansApi response:", response.data)
  return response.data
}
