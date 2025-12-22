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
  const [hasShownTempAssignmentWarning, setHasShownTempAssignmentWarning] =
    useState(false)
  const [lastAssignmentId, setLastAssignmentId] = useState<string | null>(null)

  // Store keys for dismissible notifications
  const tempAssignmentKeyRef = useRef<string | number | null>(null)
  const scalingKeyRef = useRef<string | number | null>(null)

  // Show success notification when premium instance is assigned
  useEffect(() => {
    // Show success when:
    // 1. User has a premium assignment (assigned=true, is_shared=false)
    // 2. Has an instance_id
    // 3. Either: never shown before OR different instance than last time
    // OR transitioning from temp
    const isPremiumInstance =
      assignmentResult?.assigned && !assignmentResult?.is_shared
    const hasNewInstance = assignmentResult?.instance_id !== lastAssignmentId
    const isTransitioningFromTemp =
      hasShownTempAssignmentWarning && !hasShownAssignmentSuccess

    if (
      isPremiumUser &&
      isPremiumInstance &&
      assignmentResult.instance_id &&
      (hasNewInstance || isTransitioningFromTemp)
    ) {
      // Dismiss any pending temporary assignment or scaling notifications
      if (tempAssignmentKeyRef.current) {
        closeSnackbar(tempAssignmentKeyRef.current)
        tempAssignmentKeyRef.current = null
      }
      if (scalingKeyRef.current) {
        closeSnackbar(scalingKeyRef.current)
        scalingKeyRef.current = null
      }

      enqueueSnackbar(
        "Premium instance assigned successfully! You now have dedicated compute resources.",
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
    assignmentResult,
    hasShownAssignmentSuccess,
    hasShownTempAssignmentWarning,
    lastAssignmentId,
    enqueueSnackbar,
    closeSnackbar,
  ])

  // Show temporary assignment warning when user is assigned to main instance
  useEffect(() => {
    if (
      isPremiumUser &&
      assignmentResult?.assigned &&
      assignmentResult.is_shared === true &&
      assignmentResult.assignment_source === "autoscaling_temp" &&
      !hasShownTempAssignmentWarning
    ) {
      const key = enqueueSnackbar(
        "You've been temporarily assigned to the main shared instance. " +
          "Please refrain from running workflows until transferred to your " +
          "premium instance to avoid losing progress. " +
          "This may take a few minutes.",
        {
          variant: "info",
          persist: true, // Keep notification until explicitly dismissed
        },
      )

      // Store the key so we can dismiss this notification later if needed
      tempAssignmentKeyRef.current = key

      setHasShownTempAssignmentWarning(true)
    }
  }, [
    isPremiumUser,
    assignmentResult,
    hasShownTempAssignmentWarning,
    enqueueSnackbar,
  ])

  // Show scaling notification when capacity is being scaled up
  useEffect(() => {
    if (
      isPremiumUser &&
      assignmentResult?.scaling_in_progress &&
      !assignmentResult.assigned &&
      isAssigning
    ) {
      const key = enqueueSnackbar(
        "Premium capacity is scaling up. Your dedicated instance will be ready shortly.",
        {
          variant: "info",
          autoHideDuration: 8000,
        },
      )

      // Store the key so we can dismiss this notification later if needed
      scalingKeyRef.current = key
    }
  }, [isPremiumUser, assignmentResult, isAssigning, enqueueSnackbar])

  // Show error notification for assignment failures
  useEffect(() => {
    if (isPremiumUser && error && !hasShownError) {
      // Only show critical errors, not scaling-related ones
      if (!error.includes("scaling") && !error.includes("retry")) {
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

  // Reset temp assignment warning when user is no longer on temporary instance
  useEffect(() => {
    if (
      !assignmentResult?.assigned ||
      assignmentResult.assignment_source !== "autoscaling_temp"
    ) {
      setHasShownTempAssignmentWarning(false)
      // Clear the notification key reference
      if (tempAssignmentKeyRef.current) {
        tempAssignmentKeyRef.current = null
      }
    }
  }, [assignmentResult])

  // This component doesn't render anything - it's just for notifications
  return null
}

export default PremiumNotificationManager
