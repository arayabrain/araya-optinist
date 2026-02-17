/**
 * Alert Priority Context Provider
 *
 * Manages alert prioritization to prevent overwhelming users with simultaneous
 * alerts. Shows highest priority alert as modal, others as snackbars (max 2).
 */

import React, {
  createContext,
  useContext,
  useState,
  useCallback,
  useMemo,
  ReactNode,
} from "react"

import { useSnackbar, VariantType } from "notistack"

import Alert from "@mui/material/Alert"
import Button from "@mui/material/Button"
import Dialog from "@mui/material/Dialog"
import DialogActions from "@mui/material/DialogActions"
import DialogContent from "@mui/material/DialogContent"
import DialogTitle from "@mui/material/DialogTitle"

// Alert priority levels
export type AlertPriority = "critical" | "high" | "medium" | "low"

// Priority ordering (lower number = higher priority)
const PRIORITY_ORDER: Record<AlertPriority, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
}

// Map priority to MUI severity/variant
const PRIORITY_SEVERITY: Record<AlertPriority, VariantType> = {
  critical: "error",
  high: "warning",
  medium: "info",
  low: "default",
}

const MAX_SNACKBAR_ALERTS = 2
const SNACKBAR_DURATION_LOW_MS = 5000
const SNACKBAR_DURATION_MEDIUM_MS = 8000

export interface AlertConfig {
  id: string
  priority: AlertPriority
  title?: string
  message: string
  dismissible?: boolean
  onAction?: () => void
  actionLabel?: string
}

interface AlertPriorityContextValue {
  addAlert: (alert: AlertConfig) => void
  removeAlert: (id: string) => void
  alerts: AlertConfig[]
  clearAllAlerts: () => void
}

const AlertPriorityContext = createContext<AlertPriorityContextValue | null>(
  null,
)

interface AlertPriorityProviderProps {
  children: ReactNode
}

export const AlertPriorityProvider: React.FC<AlertPriorityProviderProps> = ({
  children,
}) => {
  const [alerts, setAlerts] = useState<AlertConfig[]>([])
  const { enqueueSnackbar, closeSnackbar } = useSnackbar()
  const snackbarKeysRef = React.useRef<Map<string, string | number>>(new Map())

  const sortAlertsByPriority = useCallback((alertList: AlertConfig[]) => {
    return [...alertList].sort(
      (a, b) => PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority],
    )
  }, [])

  const addAlert = useCallback(
    (alert: AlertConfig) => {
      setAlerts((prev) => {
        // Check for duplicate ID
        if (prev.find((a) => a.id === alert.id)) {
          return prev
        }
        return sortAlertsByPriority([...prev, alert])
      })
    },
    [sortAlertsByPriority],
  )

  const removeAlert = useCallback(
    (id: string) => {
      // Close snackbar if it exists
      const snackbarKey = snackbarKeysRef.current.get(id)
      if (snackbarKey) {
        closeSnackbar(snackbarKey)
        snackbarKeysRef.current.delete(id)
      }
      setAlerts((prev) => prev.filter((a) => a.id !== id))
    },
    [closeSnackbar],
  )

  const clearAllAlerts = useCallback(() => {
    // Close all snackbars
    snackbarKeysRef.current.forEach((key) => closeSnackbar(key))
    snackbarKeysRef.current.clear()
    setAlerts([])
  }, [closeSnackbar])

  // Get top priority alert (for modal) and secondary alerts (for snackbars)
  const topAlert = alerts[0]
  const snackbarAlerts = useMemo(
    () => alerts.slice(1, 1 + MAX_SNACKBAR_ALERTS),
    [alerts],
  )

  // Show snackbars for secondary alerts
  React.useEffect(() => {
    snackbarAlerts.forEach((alert) => {
      // Skip if already showing
      if (snackbarKeysRef.current.has(alert.id)) {
        return
      }

      const key = enqueueSnackbar(alert.message, {
        variant: PRIORITY_SEVERITY[alert.priority],
        persist: alert.priority === "critical" || alert.priority === "high",
        autoHideDuration:
          alert.priority === "low"
            ? SNACKBAR_DURATION_LOW_MS
            : alert.priority === "medium"
              ? SNACKBAR_DURATION_MEDIUM_MS
              : undefined,
        action: alert.dismissible
          ? (snackbarKey) => (
              <Button
                color="inherit"
                size="small"
                onClick={() => {
                  closeSnackbar(snackbarKey)
                  removeAlert(alert.id)
                }}
              >
                Dismiss
              </Button>
            )
          : undefined,
      })
      snackbarKeysRef.current.set(alert.id, key)
    })
  }, [snackbarAlerts, enqueueSnackbar, closeSnackbar, removeAlert])

  const handleDismissModal = useCallback(() => {
    if (topAlert?.dismissible !== false) {
      removeAlert(topAlert.id)
    }
  }, [topAlert, removeAlert])

  const handleModalAction = useCallback(() => {
    if (topAlert?.onAction) {
      topAlert.onAction()
    }
    removeAlert(topAlert.id)
  }, [topAlert, removeAlert])

  const contextValue = useMemo(
    () => ({
      addAlert,
      removeAlert,
      alerts,
      clearAllAlerts,
    }),
    [addAlert, removeAlert, alerts, clearAllAlerts],
  )

  return (
    <AlertPriorityContext.Provider value={contextValue}>
      {children}

      {/* Priority Alert Modal */}
      {topAlert && (
        <Dialog
          open={true}
          onClose={
            topAlert.dismissible !== false ? handleDismissModal : undefined
          }
          aria-labelledby="priority-alert-title"
          maxWidth="sm"
          fullWidth
        >
          <DialogTitle id="priority-alert-title">
            {topAlert.title || getPriorityTitle(topAlert.priority)}
          </DialogTitle>
          <DialogContent>
            <Alert
              severity={
                PRIORITY_SEVERITY[topAlert.priority] === "default"
                  ? "info"
                  : (PRIORITY_SEVERITY[topAlert.priority] as
                      | "error"
                      | "warning"
                      | "info")
              }
              sx={{ mb: 2 }}
            >
              {topAlert.message}
            </Alert>
          </DialogContent>
          <DialogActions>
            {topAlert.dismissible !== false && (
              <Button onClick={handleDismissModal}>
                {topAlert.onAction ? "Cancel" : "OK"}
              </Button>
            )}
            {topAlert.onAction && (
              <Button
                onClick={handleModalAction}
                variant="contained"
                color={topAlert.priority === "critical" ? "error" : "primary"}
              >
                {topAlert.actionLabel || "Take Action"}
              </Button>
            )}
          </DialogActions>
        </Dialog>
      )}
    </AlertPriorityContext.Provider>
  )
}

function getPriorityTitle(priority: AlertPriority): string {
  switch (priority) {
    case "critical":
      return "Critical Alert"
    case "high":
      return "Important Notice"
    case "medium":
      return "Notice"
    case "low":
      return "Information"
  }
}

export const useAlertPriority = (): AlertPriorityContextValue => {
  const context = useContext(AlertPriorityContext)
  if (!context) {
    throw new Error(
      "useAlertPriority must be used within AlertPriorityProvider",
    )
  }
  return context
}

export default AlertPriorityProvider
