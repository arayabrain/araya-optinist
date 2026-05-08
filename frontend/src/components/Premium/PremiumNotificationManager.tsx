/**
 * Premium Notification Manager
 *
 * Handles user notifications for premium assignment status and fallback scenarios.
 */

import { FC, useEffect, useRef, useState } from "react"

import { useSnackbar } from "notistack"

import { Button } from "@mui/material"

import { logPremiumUiEvent } from "api/premium/PremiumAssignmentApi"
import { usePremiumAssignment } from "contexts/PremiumAssignmentContext"

// sessionStorage key — tracks the instance_id we've already notified about
// for this browser session. Cleared automatically when the tab closes,
// so a fresh login always shows one confirmation. Replaces the old
// `wasWaiting` gate, which incorrectly suppressed the snackbar when the
// user was already-assigned at login (no waiting popup ever shown).
const SS_NOTIFIED_INSTANCE_KEY = "premium_notified_instance_id"

function ssGetNotifiedInstance(): string | null {
  try {
    return sessionStorage.getItem(SS_NOTIFIED_INSTANCE_KEY)
  } catch {
    return null
  }
}

function ssSetNotifiedInstance(instanceId: string): void {
  try {
    sessionStorage.setItem(SS_NOTIFIED_INSTANCE_KEY, instanceId)
  } catch {
    // sessionStorage unavailable (e.g., some private browsing modes)
  }
}

const PremiumNotificationManager: FC = () => {
  const { enqueueSnackbar, closeSnackbar } = useSnackbar()
  const {
    isPremiumUser,
    assignmentResult,
    error,
    isAssigning,
    isRetryableError,
    unreachable,
  } = usePremiumAssignment()
  const { instanceUnreachable, isUnreachableTerminal } = unreachable.state
  const retryProbe = unreachable.retryProbe

  const [hasShownAssignmentSuccess, setHasShownAssignmentSuccess] =
    useState(false)
  const [hasShownError, setHasShownError] = useState(false)
  const [lastAssignmentId, setLastAssignmentId] = useState<string | null>(null)

  // Store keys for dismissible notifications
  const waitingKeyRef = useRef<string | number | null>(null)
  const unreachableKeyRef = useRef<string | number | null>(null)
  // Lets us detect a non-terminal → terminal transition to upgrade the message.
  const unreachableTerminalShownRef = useRef(false)

  const hasDedicatedInstance =
    assignmentResult?.assigned && !assignmentResult?.is_shared

  // Show success notification when premium instance is assigned.
  //
  // Gating: fire once per browser session per instance_id. We use
  // sessionStorage instead of relying on the in-component `lastAssignmentId`
  // state because that state resets on every page refresh, which would
  // re-fire the snackbar. sessionStorage clears when the tab closes,
  // so the next session sees a fresh confirmation.
  //
  // We do NOT require the waiting popup to have been shown first — that
  // gate (`wasWaiting`) used to suppress the snackbar on refresh, but it
  // also incorrectly suppressed it when the user was already-assigned at
  // login (the autoAssignOnLogin "already assigned" path skips isAssigning).
  useEffect(() => {
    const instanceId = assignmentResult?.instance_id
    const alreadyNotifiedThisSession =
      instanceId != null && ssGetNotifiedInstance() === instanceId
    const hasNewInstance = instanceId !== lastAssignmentId

    if (
      isPremiumUser &&
      hasDedicatedInstance &&
      instanceId &&
      hasNewInstance &&
      !alreadyNotifiedThisSession
    ) {
      enqueueSnackbar(
        "Premium instance assigned successfully! " +
          "You now have dedicated compute resources.",
        {
          variant: "success",
          // Longer than the default toast lifetime so a transient warning
          // popup during the dedicated ALB warm-up (issue #575) cannot
          // overwrite the confirmation before the user reads it.
          autoHideDuration: 10000,
          action: (snackbarKey) => (
            <Button
              size="small"
              color="inherit"
              onClick={() => closeSnackbar(snackbarKey)}
            >
              Dismiss
            </Button>
          ),
        },
      )
      logPremiumUiEvent("dedicated_instance_ready", {
        instance_id: instanceId,
      })

      ssSetNotifiedInstance(instanceId)
      setHasShownAssignmentSuccess(true)
      setLastAssignmentId(instanceId)
    }
  }, [
    isPremiumUser,
    hasDedicatedInstance,
    assignmentResult,
    hasShownAssignmentSuccess,
    lastAssignmentId,
    enqueueSnackbar,
    closeSnackbar,
  ])

  // Show waiting snackbar when premium user does not have a dedicated instance.
  // Two triggers: isAssigning (assignment API in flight) or assignmentResult
  // shows a shared instance. Gate prevents flash on refresh before status check.
  useEffect(() => {
    const needsWaiting =
      isAssigning || (assignmentResult && !hasDedicatedInstance)
    if (isPremiumUser && needsWaiting) {
      if (!waitingKeyRef.current) {
        const key = enqueueSnackbar(
          "Please wait while your dedicated premium resource is being prepared.",
          {
            variant: "info",
            persist: true,
          },
        )
        waitingKeyRef.current = key
        logPremiumUiEvent("waiting_popup_shown", {
          is_assigning: isAssigning,
          has_assignment: !!assignmentResult,
          is_shared: assignmentResult?.is_shared ?? null,
          instance_id: assignmentResult?.instance_id ?? null,
        })
      }
    }

    // Dismiss when: dedicated instance ready, OR assignment released/cleared
    if (
      waitingKeyRef.current &&
      (hasDedicatedInstance || (!isAssigning && !assignmentResult))
    ) {
      logPremiumUiEvent("waiting_popup_dismissed", {
        instance_id: assignmentResult?.instance_id ?? null,
        reason: hasDedicatedInstance ? "dedicated_ready" : "assignment_cleared",
      })
      closeSnackbar(waitingKeyRef.current)
      waitingKeyRef.current = null
    }
  }, [
    isPremiumUser,
    isAssigning,
    hasDedicatedInstance,
    assignmentResult,
    enqueueSnackbar,
    closeSnackbar,
  ])

  // Show error notification for assignment failures
  useEffect(() => {
    if (isPremiumUser && error && !hasShownError) {
      if (!isRetryableError) {
        // Dismiss waiting popup before showing error so they don't overlap
        if (waitingKeyRef.current) {
          logPremiumUiEvent("waiting_popup_dismissed", {
            instance_id: assignmentResult?.instance_id ?? null,
            reason: "error_shown",
          })
          closeSnackbar(waitingKeyRef.current)
          waitingKeyRef.current = null
        }

        enqueueSnackbar(
          `Premium assignment issue: ${error}. Falling back to shared resources.`,
          {
            variant: "warning",
            autoHideDuration: 10000,
          },
        )

        setHasShownError(true)
      }
    }
  }, [
    isPremiumUser,
    error,
    isRetryableError,
    hasShownError,
    assignmentResult,
    enqueueSnackbar,
    closeSnackbar,
  ])

  // Reset notification flags when user changes or errors clear
  useEffect(() => {
    if (!isPremiumUser || !error) {
      setHasShownError(false)
    }
  }, [isPremiumUser, error])

  useEffect(() => {
    if (!assignmentResult?.assigned) {
      setHasShownAssignmentSuccess(false)
    }
  }, [assignmentResult])

  useEffect(() => {
    const shouldShow =
      isPremiumUser && hasDedicatedInstance && instanceUnreachable

    const messageFor = (terminal: boolean): string =>
      terminal
        ? "Your dedicated premium instance is unresponsive after multiple attempts. Please reload the page or contact support."
        : "Your dedicated premium instance is temporarily unreachable. Retrying…"

    if (shouldShow) {
      const variantChanged =
        unreachableKeyRef.current != null &&
        unreachableTerminalShownRef.current !== isUnreachableTerminal
      if (variantChanged) {
        closeSnackbar(unreachableKeyRef.current!)
        unreachableKeyRef.current = null
      }
      if (!unreachableKeyRef.current) {
        const terminal = isUnreachableTerminal
        const key = enqueueSnackbar(messageFor(terminal), {
          variant: "warning",
          persist: true,
          action: terminal
            ? (snackbarKey) => (
                <Button
                  size="small"
                  color="inherit"
                  onClick={() => {
                    closeSnackbar(snackbarKey)
                    retryProbe()
                  }}
                >
                  Retry
                </Button>
              )
            : undefined,
        })
        unreachableKeyRef.current = key
        unreachableTerminalShownRef.current = terminal
        logPremiumUiEvent("instance_unreachable_popup_shown", {
          instance_id: assignmentResult?.instance_id ?? null,
          terminal,
        })
      }
    }

    // Dismiss whenever shouldShow goes false (recovered / non-dedicated / non-premium).
    if (unreachableKeyRef.current && !shouldShow) {
      logPremiumUiEvent("instance_unreachable_popup_dismissed", {
        instance_id: assignmentResult?.instance_id ?? null,
        reason: !isPremiumUser
          ? "not_premium"
          : !hasDedicatedInstance
            ? "not_dedicated"
            : "reachable",
      })
      closeSnackbar(unreachableKeyRef.current)
      unreachableKeyRef.current = null
      unreachableTerminalShownRef.current = false
    }
  }, [
    isPremiumUser,
    hasDedicatedInstance,
    instanceUnreachable,
    isUnreachableTerminal,
    assignmentResult,
    enqueueSnackbar,
    closeSnackbar,
    retryProbe,
  ])

  // Cleanup waiting notification on unmount
  useEffect(() => {
    return () => {
      if (waitingKeyRef.current) {
        closeSnackbar(waitingKeyRef.current)
      }
      if (unreachableKeyRef.current) {
        closeSnackbar(unreachableKeyRef.current)
      }
    }
  }, [closeSnackbar])

  return null
}

export default PremiumNotificationManager
