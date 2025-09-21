/**
 * Premium Notification Manager
 *
 * Handles user notifications for premium assignment status and fallback scenarios.
 */

import { FC, useEffect, useState } from "react"

import { useSnackbar } from "notistack"

import { usePremiumAssignment } from "contexts/PremiumAssignmentContext"

const PremiumNotificationManager: FC = () => {
  const { enqueueSnackbar } = useSnackbar()
  const { isPremiumUser, assignmentResult, error, isAssigning } =
    usePremiumAssignment()

  const [hasShownAssignmentSuccess, setHasShownAssignmentSuccess] =
    useState(false)
  const [hasShownError, setHasShownError] = useState(false)
  const [lastAssignmentId, setLastAssignmentId] = useState<string | null>(null)

  // Show success notification when premium instance is assigned
  useEffect(() => {
    if (
      isPremiumUser &&
      assignmentResult?.assigned &&
      assignmentResult.instance_id &&
      assignmentResult.instance_id !== lastAssignmentId &&
      !hasShownAssignmentSuccess
    ) {
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
    lastAssignmentId,
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
      enqueueSnackbar(
        "Premium capacity is scaling up. Your dedicated instance will be ready shortly.",
        {
          variant: "info",
          autoHideDuration: 8000,
        },
      )
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

  // This component doesn't render anything - it's just for notifications
  return null
}

export default PremiumNotificationManager
