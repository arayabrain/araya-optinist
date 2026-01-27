import React, { useEffect, useState } from "react"
import { useDispatch } from "react-redux"
import { useNavigate } from "react-router-dom"

import { CheckoutValidationStatus } from "api/subscriptions/SubscriptionsApiDTO"
import Loading from "components/common/Loading"
import { validateSession } from "components/utils/ValidateCheckoutSession"
import PaymentResult from "pages/Subscription/payment_result"
import { AppDispatch } from "store/store"

const Failed: React.FC = () => {
  const dispatch = useDispatch<AppDispatch>()
  const navigate = useNavigate()
  const searchParams = new URLSearchParams(window.location.search)
  const sessionId = searchParams.get("session_id")

  const [validationStatus, setValidationStatus] =
    useState<CheckoutValidationStatus | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    validateSession(
      sessionId,
      setValidationStatus,
      setIsLoading,
      dispatch,
      navigate,
      false,
    )
  }, [dispatch, sessionId, navigate])

  if (isLoading) {
    return <Loading loading={true} />
  }

  // On the failed page, show payment_failed regardless of validation status
  if (validationStatus) {
    return <PaymentResult type={CheckoutValidationStatus.PAYMENT_FAILED} />
  }

  return null
}

export default Failed
