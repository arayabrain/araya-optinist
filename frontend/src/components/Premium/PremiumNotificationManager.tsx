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
  const { isPremiumUser, assignmentResult, error } = usePremiumAssignment()

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
  useEffect(() => {
    if (isPremiumUser && assignmentResult && !hasDedicatedInstance) {
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
          has_assignment: !!assignmentResult,
          is_shared: assignmentResult?.is_shared ?? null,
          instance_id: assignmentResult?.instance_id ?? null,
        })
      }
    }

    if (hasDedicatedInstance && waitingKeyRef.current) {
      logPremiumUiEvent("waiting_popup_dismissed", {
        instance_id: assignmentResult?.instance_id ?? null,
      })
      closeSnackbar(waitingKeyRef.current)
      waitingKeyRef.current = null
    }
  }, [
    isPremiumUser,
    hasDedicatedInstance,
    assignmentResult,
    enqueueSnackbar,
    closeSnackbar,
  ])

  // Show error notification for assignment failures
  useEffect(() => {
    if (isPremiumUser && error && !hasShownError) {
      if (!error.includes("scaling") && !error.includes("retry")) {
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
  }, [isPremiumUser, error, hasShownError, enqueueSnackbar])

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
