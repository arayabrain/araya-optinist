import { useEffect, useState, useCallback, useRef } from "react"

import { useSnackbar, SnackbarKey } from "notistack"

import {
  getMyLimitAlertApi,
  checkLimitAlertStatusApi,
  LimitAlert,
  LimitAlertStatus,
} from "api/storage/StorageAlerts"
import { LimitAlertType } from "const/Subscription"

// Constants for retry logic (Case 30)
const MAX_CONSECUTIVE_FAILURES = 3
const VISIBILITY_CHECK_DEBOUNCE_MS = 5000

/**
 * Enum for detailed alert context classification.
 * Provides more granular categorization than just alert_type.
 */
export enum AlertContext {
  NONE = "none",
  FREE_USER_STORAGE_EXCEEDED = "free_user_storage_exceeded",
  PREMIUM_STORAGE_EXCEEDED = "premium_storage_exceeded",
  PREMIUM_GRACE_PERIOD = "premium_grace_period",
  PREMIUM_OVERDUE = "premium_overdue",
}

/**
 * Determines the detailed alert context based on alert data.
 * Case 36 fix: Explicitly distinguishes premium users with storage issues
 * from free users to prevent incorrect UI decisions.
 */
export const determineAlertContext = (
  alert: LimitAlert | null,
): AlertContext => {
  if (!alert || !alert.has_alert) {
    return AlertContext.NONE
  }

  const hasStorageIssue = alert.excess_data_gb > 0
  const hasDeletionDate = !!alert.deletion_date
  const hasSubscriptionEndDate = !!alert.subscription_end_date
  const hasGraceEndDate = !!alert.grace_end_date

  switch (alert.alert_type) {
    case LimitAlertType.STORAGE:
      // STORAGE type with subscription_end_date = premium user
      // STORAGE type with deletion_date but no subscription_end_date = free user
      if (hasSubscriptionEndDate && !hasDeletionDate) {
        return AlertContext.PREMIUM_STORAGE_EXCEEDED
      }
      return AlertContext.FREE_USER_STORAGE_EXCEEDED

    case LimitAlertType.GRACE:
      return AlertContext.PREMIUM_GRACE_PERIOD

    case LimitAlertType.OVERDUE:
      return AlertContext.PREMIUM_OVERDUE

    default:
      // Fallback: infer from fields
      if (hasGraceEndDate || hasSubscriptionEndDate) {
        if (hasStorageIssue && !hasDeletionDate) {
          return AlertContext.PREMIUM_STORAGE_EXCEEDED
        }
        return AlertContext.PREMIUM_GRACE_PERIOD
      }
      if (hasDeletionDate && hasStorageIssue) {
        return AlertContext.FREE_USER_STORAGE_EXCEEDED
      }
      return AlertContext.NONE
  }
}

/**
 * Returns whether the alert context indicates a premium user issue.
 */
export const isPremiumUserAlert = (context: AlertContext): boolean => {
  return (
    context === AlertContext.PREMIUM_STORAGE_EXCEEDED ||
    context === AlertContext.PREMIUM_GRACE_PERIOD ||
    context === AlertContext.PREMIUM_OVERDUE
  )
}

/**
 * Returns whether the alert requires immediate action (upgrade/deletion imminent).
 */
export const requiresImmediateAction = (
  alert: LimitAlert | null,
  context: AlertContext,
): boolean => {
  if (!alert || context === AlertContext.NONE) return false
  return (
    context === AlertContext.PREMIUM_OVERDUE ||
    (alert.days_remaining !== undefined && alert.days_remaining <= 7)
  )
}

interface UseLimitAlertReturn {
  alert: LimitAlert | null
  alertContext: AlertContext
  hasAlert: boolean
  loading: boolean
  error: Error | null
  consecutiveFailures: number
  checkLimitAlert: () => Promise<boolean>
  dismissAlert: () => void
  retryNow: () => void
}

interface UseLimitAlertOptions {
  autoCheck?: boolean
  checkInterval?: number // in milliseconds
  showSnackbar?: boolean
  showModalOnLogin?: boolean
  checkOnVisibilityChange?: boolean // Case 38 fix
}

export const useLimitAlert = (
  options: UseLimitAlertOptions = {},
): UseLimitAlertReturn => {
  const {
    autoCheck = true,
    checkInterval = 10 * 60 * 1000, // 10 minutes default
    showSnackbar = false,
    showModalOnLogin: _showModalOnLogin = false,
    checkOnVisibilityChange = true, // Default: true for responsive updates
  } = options

  const { enqueueSnackbar, closeSnackbar } = useSnackbar()
  const [alert, setAlert] = useState<LimitAlert | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [consecutiveFailures, setConsecutiveFailures] = useState(0)
  const lastCheckRef = useRef<number>(0)
  const errorSnackbarKeyRef = useRef<SnackbarKey | null>(null)
  const [dismissed, setDismissed] = useState(() => {
    const dismissedAlerts = localStorage.getItem("dismissedAlerts")
    if (dismissedAlerts) {
      try {
        const parsed = JSON.parse(dismissedAlerts)
        return parsed.limitAlert === true
      } catch {
        return false
      }
    }
    return false
  })

  const showErrorSnackbar = useCallback(() => {
    // Close existing error snackbar if any
    if (errorSnackbarKeyRef.current) {
      closeSnackbar(errorSnackbarKeyRef.current)
    }

    const key = enqueueSnackbar(
      "Unable to check alerts. Please refresh the page.",
      {
        variant: "warning",
        persist: true,
      },
    )
    errorSnackbarKeyRef.current = key
  }, [enqueueSnackbar, closeSnackbar])

  const checkLimitAlert = useCallback(async (): Promise<boolean> => {
    try {
      setLoading(true)
      lastCheckRef.current = Date.now()
      const alertResponse = await getMyLimitAlertApi()

      // Reset error state on success (Case 30)
      setError(null)
      setConsecutiveFailures(0)
      if (errorSnackbarKeyRef.current) {
        closeSnackbar(errorSnackbarKeyRef.current)
        errorSnackbarKeyRef.current = null
      }

      if (alertResponse && alertResponse.has_alert) {
        const newAlert = alertResponse

        if (
          showSnackbar &&
          (!alert || newAlert.alert_type !== alert.alert_type)
        ) {
          const severity =
            newAlert.alert_type === LimitAlertType.OVERDUE ? "error" : "warning"
          const autoHideDuration =
            newAlert.alert_type === LimitAlertType.OVERDUE ? 15000 : 10000

          enqueueSnackbar(
            `Account Alert: ${newAlert.days_remaining} days remaining to resolve storage issue`,
            {
              variant: severity,
              autoHideDuration,
            },
          )
        }

        setAlert(newAlert)
        return true
      } else {
        setAlert(null)
        return false
      }
    } catch (err) {
      const errorObj = err instanceof Error ? err : new Error(String(err))
      setError(errorObj)

      // Increment consecutive failure count (Case 30)
      setConsecutiveFailures((prev) => {
        const newCount = prev + 1
        // Show error snackbar after MAX_CONSECUTIVE_FAILURES
        if (newCount >= MAX_CONSECUTIVE_FAILURES) {
          showErrorSnackbar()
        }
        return newCount
      })

      setAlert(null)
      return false
    } finally {
      setLoading(false)
    }
  }, [alert, showSnackbar, enqueueSnackbar, closeSnackbar, showErrorSnackbar])

  const retryNow = useCallback(() => {
    setConsecutiveFailures(0)
    setError(null)
    if (errorSnackbarKeyRef.current) {
      closeSnackbar(errorSnackbarKeyRef.current)
      errorSnackbarKeyRef.current = null
    }
    checkLimitAlert()
  }, [checkLimitAlert, closeSnackbar])

  const dismissAlert = useCallback(() => {
    const dismissedAlerts = localStorage.getItem("dismissedAlerts")
    let parsed = {}
    try {
      parsed = dismissedAlerts ? JSON.parse(dismissedAlerts) : {}
    } catch {
      // Handle JSON parse errors
    }

    localStorage.setItem(
      "dismissedAlerts",
      JSON.stringify({
        ...parsed,
        limitAlert: true,
      }),
    )

    setDismissed(true)
  }, [])

  // Case 38 fix: Check on visibility change for responsive updates after tab returns
  useEffect(() => {
    if (!checkOnVisibilityChange || !autoCheck || dismissed) return

    const handleVisibilityChange = () => {
      if (document.visibilityState !== "visible") return

      const timeSinceLastCheck = Date.now() - lastCheckRef.current
      if (timeSinceLastCheck >= VISIBILITY_CHECK_DEBOUNCE_MS) {
        checkLimitAlert()
      }
    }

    document.addEventListener("visibilitychange", handleVisibilityChange)
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange)
    }
  }, [checkOnVisibilityChange, autoCheck, dismissed, checkLimitAlert])

  useEffect(() => {
    if (autoCheck && !dismissed) {
      checkLimitAlert()

      const interval = setInterval(checkLimitAlert, checkInterval)
      return () => clearInterval(interval)
    }
    return () => {}
  }, [autoCheck, checkInterval, checkLimitAlert, dismissed])

  const currentAlert = dismissed ? null : alert

  return {
    alert: currentAlert,
    alertContext: determineAlertContext(currentAlert),
    hasAlert: Boolean(alert?.has_alert && !dismissed),
    loading,
    error,
    consecutiveFailures,
    checkLimitAlert,
    dismissAlert,
    retryNow,
  }
}

/**
 * Quick hook to check if user has any limit alerts without full details
 */
export const useLimitAlertStatus = () => {
  const [status, setStatus] = useState<LimitAlertStatus | null>(null)
  const [loading, setLoading] = useState(false)

  const checkStatus = useCallback(async () => {
    try {
      setLoading(true)
      const statusResponse = await checkLimitAlertStatusApi()
      setStatus(statusResponse)
    } catch (error) {
      // console.error("Failed to check limit alert status:", error)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    checkStatus()
  }, [checkStatus])

  return {
    status,
    hasAlert: Boolean(status?.has_alert),
    loading,
    checkStatus,
  }
}
