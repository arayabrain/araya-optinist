import { useEffect, useState, useCallback } from "react"

import { useSnackbar } from "notistack"

import {
  getMyLimitAlertApi,
  checkLimitAlertStatusApi,
  LimitAlert,
  LimitAlertStatus,
} from "api/storage/StorageAlerts"

interface UseLimitAlertReturn {
  alert: LimitAlert | null
  hasAlert: boolean
  loading: boolean
  checkLimitAlert: () => Promise<boolean>
  dismissAlert: () => void
}

interface UseLimitAlertOptions {
  autoCheck?: boolean
  checkInterval?: number // in milliseconds
  showSnackbar?: boolean
  showModalOnLogin?: boolean
}

export const useLimitAlert = (
  options: UseLimitAlertOptions = {},
): UseLimitAlertReturn => {
  const {
    autoCheck = true,
    checkInterval = 10 * 60 * 1000, // 10 minutes default (less frequent than storage alerts)
    showSnackbar = false,
    showModalOnLogin: _showModalOnLogin = false,
  } = options

  const { enqueueSnackbar } = useSnackbar()
  const [alert, setAlert] = useState<LimitAlert | null>(null)
  const [loading, setLoading] = useState(false)
  const [dismissed, setDismissed] = useState(() => {
    // Check if this alert was already dismissed in localStorage
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

  const checkLimitAlert = useCallback(async (): Promise<boolean> => {
    try {
      setLoading(true)
      const alertResponse = await getMyLimitAlertApi()

      if (alertResponse && alertResponse.has_alert) {
        const newAlert = alertResponse

        // Show snackbar notification if this is a new alert or severity changed
        if (
          showSnackbar &&
          (!alert || newAlert.alert_type !== alert.alert_type)
        ) {
          const severity =
            newAlert.alert_type === "overdue" ? "error" : "warning"
          const autoHideDuration =
            newAlert.alert_type === "overdue" ? 15000 : 10000

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
    } catch (error) {
      // console.error("Failed to check limit alert:", error)
      // Silently fail to not disrupt user experience
      setAlert(null)
      return false
    } finally {
      setLoading(false)
    }
  }, [alert, showSnackbar, enqueueSnackbar])

  const dismissAlert = useCallback(() => {
    // Persist dismissal in localStorage
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

  useEffect(() => {
    if (autoCheck && !dismissed) {
      // Initial check
      checkLimitAlert()

      // Set up interval for periodic checks
      const interval = setInterval(checkLimitAlert, checkInterval)

      return () => clearInterval(interval)
    }
    return () => {}
  }, [autoCheck, checkInterval, checkLimitAlert, dismissed])

  return {
    alert: dismissed ? null : alert,
    hasAlert: Boolean(alert?.has_alert && !dismissed),
    loading,
    checkLimitAlert,
    dismissAlert,
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
