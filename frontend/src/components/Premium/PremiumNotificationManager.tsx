/**
 * Premium Notification Manager
 *
 * Handles user notifications for premium assignment status and fallback scenarios.
 */

import { FC, useEffect, useRef, useState } from "react"

import { useSnackbar } from "notistack"

import { logPremiumUiEvent } from "api/premium/PremiumAssignmentApi"
import { usePremiumAssignment } from "contexts/PremiumAssignmentContext"

const PremiumNotificationManager: FC = () => {
  const { enqueueSnackbar, closeSnackbar } = useSnackbar()
  const {
    isPremiumUser,
    assignmentResult,
    error,
    isAssigning,
    isRetryableError,
  } = usePremiumAssignment()

  const [hasShownAssignmentSuccess, setHasShownAssignmentSuccess] =
    useState(false)
  const [hasShownError, setHasShownError] = useState(false)
  const [lastAssignmentId, setLastAssignmentId] = useState<string | null>(null)

  // Store keys for dismissible notifications
  const waitingKeyRef = useRef<string | number | null>(null)

  const hasDedicatedInstance =
    assignmentResult?.assigned && !assignmentResult?.is_shared

  // Show success notification when premium instance is assigned
  useEffect(() => {
    const hasNewInstance = assignmentResult?.instance_id !== lastAssignmentId
    const wasWaiting = waitingKeyRef.current !== null

    if (
      isPremiumUser &&
      hasDedicatedInstance &&
      assignmentResult.instance_id &&
      hasNewInstance &&
      wasWaiting
    ) {
      enqueueSnackbar(
        "Premium instance assigned successfully! " +
          "You now have dedicated compute resources.",
        {
          variant: "success",
          autoHideDuration: 5000,
        },
      )
      logPremiumUiEvent("dedicated_instance_ready", {
        instance_id: assignmentResult.instance_id,
      })

      setHasShownAssignmentSuccess(true)
      setLastAssignmentId(assignmentResult.instance_id)
    }
  }, [
    isPremiumUser,
    hasDedicatedInstance,
    assignmentResult,
    hasShownAssignmentSuccess,
    lastAssignmentId,
    enqueueSnackbar,
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
          "Please wait while your dedicated premium " +
            "resource is being prepared.",
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
          "Premium assignment issue: " +
            `${error}. Falling back to shared resources.`,
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

  // Cleanup waiting notification on unmount
  useEffect(() => {
    return () => {
      if (waitingKeyRef.current) {
        closeSnackbar(waitingKeyRef.current)
      }
    }
  }, [closeSnackbar])

  return null
}

export default PremiumNotificationManager
