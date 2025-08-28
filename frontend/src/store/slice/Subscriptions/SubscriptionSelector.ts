import { RootState } from "store/store"

export const selectSubscriptionPlans = (state: RootState) =>
  state.subscription.plans
export const selectUserSubscription = (state: RootState) =>
  state.subscription.userSubscription
export const selectSubscriptionLoading = (state: RootState) =>
  state.subscription.loading
export const selectSubscriptionError = (state: RootState) =>
  state.subscription.error
export const selectPlansLoading = (state: RootState) =>
  state.subscription.plansLoading
export const selectUserSubscriptionLoading = (state: RootState) =>
  state.subscription.userSubscriptionLoading

export const selectSubscriptionExpirationDate = (state: RootState) => {
  const userSubscription = selectUserSubscription(state)
  return userSubscription?.expiration
}

// Derived selectors
export const selectIsSubscriptionExpired = (state: RootState) => {
  const userSubscription = selectUserSubscription(state)
  if (!userSubscription) return false

  const now = new Date()
  const expirationDate = new Date(userSubscription.expiration)
  return expirationDate <= now
}

export const selectCurrentPlanId = (state: RootState) => {
  const userSubscription = selectUserSubscription(state)
  const isExpired = selectIsSubscriptionExpired(state)

  if (!userSubscription || isExpired) {
    const plans = selectSubscriptionPlans(state)
    return plans.find((p: { name: string }) => p.name === "Free")?.id
  }

  return userSubscription.plan_id
}

export const selectCheckoutLoading = (state: RootState) =>
  state.subscription.checkoutLoading
