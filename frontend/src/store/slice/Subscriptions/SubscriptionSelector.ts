import { RootState } from "store/store"

export const selectSubscriptionPlans = (state: RootState) =>
  state.subscription.plans
export const selectUserSubscription = (state: RootState) => {
  return state.subscription.userSubscription
}
export const selectSubscriptionLoading = (state: RootState) =>
  state.subscription.loading
export const selectSubscriptionError = (state: RootState) =>
  state.subscription.error
export const selectPlansLoading = (state: RootState) =>
  state.subscription.plansLoading
export const selectUserSubscriptionLoading = (state: RootState) =>
  state.subscription.userSubscriptionLoading
export const selectCheckoutLoading = (state: RootState) =>
  state.subscription.checkoutLoading
export const selectIsSubscriptionExpired = (state: RootState) =>
  state.subscription.userSubscription?.is_expired
export const selectServerTime = (state: RootState) =>
  state.subscription.serverTime

export const selectSubscriptionExpirationDate = (state: RootState) => {
  const userSubscription = selectUserSubscription(state)
  return userSubscription?.expiration
}

export const selectCurrentPlanId = (state: RootState) => {
  const userSubscription = selectUserSubscription(state)
  const isExpired = selectIsSubscriptionExpired(state)

  if (!userSubscription || isExpired) {
    const plans = selectSubscriptionPlans(state)
    return plans.find((p) => p.price === 0)?.id
  }

  return userSubscription.plan_id
}
