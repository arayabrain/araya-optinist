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

const WEBHOOK_RETRY_COUNT = 3
const WEBHOOK_RETRY_BASE_DELAY_MS = 3000

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

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
    let response = result.payload as CheckoutValidationResponse
    if (response && response.status) {
      // Retry on WEBHOOK_FAILED for the thanks page (webhook race condition)
      if (
        isThanksPage &&
        response.status === CheckoutValidationStatus.WEBHOOK_FAILED
      ) {
        for (let i = 0; i < WEBHOOK_RETRY_COUNT; i++) {
          await sleep(WEBHOOK_RETRY_BASE_DELAY_MS * (i + 1))
          const retryResult = await dispatch(
            validateCheckoutSessionApi(sessionId),
          )
          const retryResponse =
            retryResult.payload as CheckoutValidationResponse
          if (
            retryResponse?.status &&
            retryResponse.status !== CheckoutValidationStatus.WEBHOOK_FAILED
          ) {
            response = retryResponse
            break
          }
          // Update response for final attempt so we show the latest status
          if (retryResponse?.status) {
            response = retryResponse
          }
        }
      }

      setIsValidSession(response.status)
      setIsLoading(false)
    } else {
      // Invalid session_id - redirect without changing loading state
      // Keep showing loading spinner until redirect completes
      // eslint-disable-next-line no-console
      console.error("Invalid session_id:", sessionId)
      navigate("/subscription", { replace: true })
    }
  } catch (error) {
    // Invalid session_id - redirect without changing loading state
    // Keep showing loading spinner until redirect completes
    // eslint-disable-next-line no-console
    console.error("Invalid session_id:", sessionId, error)
    navigate("/subscription", { replace: true })
  }
}
