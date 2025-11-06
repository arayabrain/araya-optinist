import React, { useEffect, useState } from "react"
import { useDispatch } from "react-redux"
import { useNavigate } from "react-router-dom"

import Loading from "components/common/Loading"
import { validateSession } from "components/utils/ValidateCheckoutSession"
import PaymentResult from "pages/Subscription/payment_result"
import { AppDispatch } from "store/store"

const Failed: React.FC = () => {
  const dispatch = useDispatch<AppDispatch>()
  const navigate = useNavigate()
  const searchParams = new URLSearchParams(window.location.search)
  const sessionId = searchParams.get("session_id")

  const [isValidSession, setIsValidSession] = useState(false)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    validateSession(
      sessionId,
      setIsValidSession,
      setIsLoading,
      dispatch,
      navigate,
      false,
    )
  }, [dispatch, sessionId, navigate])

  if (isLoading) {
    return <Loading loading={true} />
  }

  if (isValidSession) {
    return <PaymentResult type="failed" />
  }

  return null
}

export default Failed
