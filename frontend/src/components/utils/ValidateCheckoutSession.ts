import React from "react"
import { NavigateFunction } from "react-router-dom"

import {
  validateCheckoutSessionApi,
  validateFailedCheckoutSessionApi,
} from "api/subscriptions/Subscriptions"
import { AppDispatch } from "store/store"

export const validateSession = async (
  sessionId: string | null,
  setIsValidSession: React.Dispatch<React.SetStateAction<boolean>>,
  setIsLoading: React.Dispatch<React.SetStateAction<boolean>>,
  dispatch: AppDispatch,
  navigate: NavigateFunction,
  isThanksPage?: boolean,
) => {
  // If no session ID, redirect to checkout
  if (!sessionId) {
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
    if (result.payload === true || result.meta?.requestStatus === "fulfilled") {
      setIsValidSession(true)
    } else {
      navigate("/subscription", { replace: true })
    }
  } catch (error) {
    navigate("/subscription", { replace: true })
  } finally {
    setIsLoading(false)
  }
}
