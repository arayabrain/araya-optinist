import React, { useEffect, useState } from "react"
import { useDispatch } from "react-redux"
import { useNavigate } from "react-router-dom"

import { validateFailedCheckoutSessionApi } from "api/subscriptions/Subscriptions"
import Loading from "components/common/Loading"
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
    const validateSession = async () => {
      // If no session ID, redirect to checkout
      if (!sessionId) {
        navigate("/console/subscription", { replace: true })
        return
      }

      try {
        // Dispatch the failed session validation action
        const result = await dispatch(
          await validateFailedCheckoutSessionApi(sessionId),
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
        console.error("Session validation failed:", error)
        navigate("/console/subscription", { replace: true })
      } finally {
        setIsLoading(false)
      }
    }

    validateSession()
  }, [dispatch, sessionId, navigate])

  // Show loading while validating
  if (isLoading) {
    return <Loading loading={true} />
  }

  // Only render PaymentResult if session is valid
  if (isValidSession) {
    return <PaymentResult type="failed" />
  }

  // Return null if session is invalid (navigation will handle redirect)
  return null
}

export default Failed
