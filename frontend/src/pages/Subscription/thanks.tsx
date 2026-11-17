import React, { useEffect, useState } from "react"
import { useDispatch, useSelector } from "react-redux"
import { useNavigate } from "react-router-dom"

import { CheckoutValidationStatus } from "api/subscriptions/SubscriptionsApiDTO"
import Loading from "components/common/Loading"
import { validateSession } from "components/utils/ValidateCheckoutSession"
import PaymentResult from "pages/Subscription/payment_result"
import { getUserSubscription } from "store/slice/Subscriptions/SubscriptionActions"
import { selectUserSubscription } from "store/slice/Subscriptions/SubscriptionSelector"
import { AppDispatch } from "store/store"

const Thanks: React.FC = () => {
  const dispatch = useDispatch<AppDispatch>()
  const navigate = useNavigate()
  const searchParams = new URLSearchParams(window.location.search)
  const sessionId = searchParams.get("session_id")
  const userSubscription = useSelector(selectUserSubscription)

  const [validationStatus, setValidationStatus] =
    useState<CheckoutValidationStatus | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [subscriptionLoaded, setSubscriptionLoaded] = useState(false)

  useEffect(() => {
    validateSession(
      sessionId,
      setValidationStatus,
      setIsLoading,
      dispatch,
      navigate,
      true,
    )
  }, [dispatch, sessionId, navigate])

  // The new subscription tells whether this checkout started a trial
  useEffect(() => {
    if (validationStatus !== CheckoutValidationStatus.SUCCESS) return
    dispatch(getUserSubscription()).finally(() => setSubscriptionLoaded(true))
  }, [dispatch, validationStatus])

  if (
    isLoading ||
    (validationStatus === CheckoutValidationStatus.SUCCESS &&
      !subscriptionLoaded)
  ) {
    return <Loading loading={true} />
  }

  switch (validationStatus) {
    case CheckoutValidationStatus.SUCCESS:
      return (
        <PaymentResult
          type={CheckoutValidationStatus.SUCCESS}
          trialEnd={
            userSubscription?.is_trial ? userSubscription.trial_end : null
          }
        />
      )
    case CheckoutValidationStatus.PAYMENT_FAILED:
      return <PaymentResult type={CheckoutValidationStatus.PAYMENT_FAILED} />
    case CheckoutValidationStatus.WEBHOOK_FAILED:
    default:
      // Handle unexpected cases
      return <PaymentResult type={CheckoutValidationStatus.WEBHOOK_FAILED} />
  }
}

export default Thanks
