/**
 * Premium Assignment Manager
 *
 * Component that handles automatic premium instance assignment for premium users.
 * Should be included in the app layout to run in the background.
 */

import { FC, useEffect } from "react"
import { useSelector } from "react-redux"

import { usePremiumAssignment } from "hooks/usePremiumAssignment"
import { selectCurrentUser } from "store/slice/User/UserSelector"

const PremiumAssignmentManager: FC = () => {
  const currentUser = useSelector(selectCurrentUser)
  const {
    isPremiumUser,
    autoAssignOnLogin,
    autoReleaseOnLogout,
    assignmentResult,
    error,
  } = usePremiumAssignment()

  // Handle premium assignment on user login
  useEffect(() => {
    if (isPremiumUser && currentUser) {
      // eslint-disable-next-line no-console
      console.log("Premium user detected, triggering auto-assignment...")
      autoAssignOnLogin()
    }
  }, [isPremiumUser, currentUser, autoAssignOnLogin])

  // Handle cleanup on component unmount (app close/logout)
  useEffect(() => {
    return () => {
      if (isPremiumUser) {
        // Don't await this - just fire and forget on unmount
        autoReleaseOnLogout()
      }
    }
  }, [isPremiumUser, autoReleaseOnLogout])

  // Log assignment status for debugging
  useEffect(() => {
    if (assignmentResult) {
      if (assignmentResult.assigned) {
        // eslint-disable-next-line no-console
        console.log(
          "Premium instance assigned successfully:",
          assignmentResult.instance_id,
        )
      } else if (assignmentResult.scaling_in_progress) {
        // eslint-disable-next-line no-console
        console.log(
          "Premium capacity scaling in progress, will retry automatically",
        )
      }
    }

    if (error) {
      // eslint-disable-next-line no-console
      console.warn("Premium assignment error:", error)
    }
  }, [assignmentResult, error])

  return null
}

export default PremiumAssignmentManager
