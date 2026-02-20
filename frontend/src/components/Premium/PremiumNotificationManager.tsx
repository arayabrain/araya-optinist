/**
 * Premium Notification Manager
 *
 * Handles user notifications for premium assignment status and fallback scenarios.
 */

import { FC, useEffect, useRef, useState } from "react"

import { useSnackbar } from "notistack"

import { usePremiumAssignment } from "contexts/PremiumAssignmentContext"

const PremiumNotificationManager: FC = () => {
  const { enqueueSnackbar, closeSnackbar } = useSnackbar()
  const { isPremiumUser, assignmentResult, error, isAssigning } =
    usePremiumAssignment()

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
      (hasNewInstance || wasWaiting)
    ) {
      enqueueSnackbar(
        "Premium instance assigned successfully! " +
          "You now have dedicated compute resources.",
        {
          variant: "success",
          autoHideDuration: 5000,
        },
      )

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

  // Show waiting snackbar when premium user does not have
  // a dedicated instance (covers shared, scaling, not-yet-assigned)
  useEffect(() => {
    if (isPremiumUser && !hasDedicatedInstance && !isAssigning) {
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
      }
    }

    if (hasDedicatedInstance && waitingKeyRef.current) {
      closeSnackbar(waitingKeyRef.current)
      waitingKeyRef.current = null
    }
  }, [
    isPremiumUser,
    hasDedicatedInstance,
    isAssigning,
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
