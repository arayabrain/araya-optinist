import React from "react"
import { NavigateFunction } from "react-router-dom"

import {
  validateCheckoutSessionApi,
  validateFailedCheckoutSessionApi,
} from "api/subscriptions/Subscriptions"
import {
  CheckoutValidationResponse,
  CheckoutValidationStatus,
} from "api/subscriptions/SubscriptionsApiDTO"
import { AppDispatch } from "store/store"

export const validateSession = async (
  sessionId: string | null,
  setIsValidSession: React.Dispatch<
    React.SetStateAction<CheckoutValidationStatus | null>
  >,
  setIsLoading: React.Dispatch<React.SetStateAction<boolean>>,
  dispatch: AppDispatch,
  navigate: NavigateFunction,
  isThanksPage?: boolean,
) => {
  // If no session ID or empty string, redirect to subscription page
  if (!sessionId || sessionId.trim() === "") {
    navigate("/subscription", { replace: true })
    return
  }

  try {
    // Dispatch the validation action
    let result
    if (isThanksPage) {
      result = await dispatch(validateCheckoutSessionApi(sessionId))
    } else {
      result = await dispatch(validateFailedCheckoutSessionApi(sessionId))
    }

    // Check if the validation was successful
    const response = result.payload as CheckoutValidationResponse
    if (response && response.status) {
      setIsValidSession(response.status)
    } else {
      // Invalid session_id - log error and redirect
      console.error("Invalid session_id:", sessionId)
      navigate("/subscription", { replace: true })
    }
  } catch (error) {
    // Invalid session_id - log error and redirect
    console.error("Invalid session_id:", sessionId, error)
    navigate("/subscription", { replace: true })
  } finally {
    setIsLoading(false)
  }
}
