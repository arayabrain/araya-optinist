import React, { useEffect, useState } from "react"
import { useDispatch } from "react-redux"
import { useNavigate } from "react-router-dom"

import { validateCheckoutSessionApi } from "api/subscriptions/Subscriptions"
import Loading from "components/common/Loading"
import PaymentResult from "pages/Subscription/payment_result"
import { AppDispatch } from "store/store"

const Thanks: React.FC = () => {
  const dispatch = useDispatch<AppDispatch>()
  const navigate = useNavigate()
  const searchParams = new URLSearchParams(window.location.search)
  const sessionId = searchParams.get("session_id")

  const [isValidSession, setIsValidSession] = useState(false)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    const validateSession = async () => {
      // If no session ID, redirect to checkout
      if (!sessionId) {
        navigate("/console/subscription", { replace: true })
        return
      }

      try {
        // Dispatch the validation action
        const result = await dispatch(
          await validateCheckoutSessionApi(sessionId),
        )

        // Check if the validation was successful
        // Adjust this based on how your Redux action returns data
        if (
          result.payload === true ||
          result.meta?.requestStatus === "fulfilled"
        ) {
          setIsValidSession(true)
        } else {
          navigate("/console/subscription", { replace: true })
        }
      } catch (error) {
        navigate("/console/subscription", { replace: true })
      } finally {
        setIsLoading(false)
      }
    }

    validateSession()
  }, [dispatch, sessionId, navigate])

  if (isLoading) {
    return <Loading loading={true} />
  }

  if (isValidSession) {
    return <PaymentResult type="success" />
  }

  return null
}

export default Thanks
