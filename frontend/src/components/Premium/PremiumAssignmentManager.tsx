/**
 * Premium Assignment Manager
 *
 * Component that handles cleanup and logging for premium assignments.
 * Assignment logic is handled by PremiumAssignmentContext.
 */

import { FC, useEffect, useRef } from "react"

import { usePremiumAssignment } from "contexts/PremiumAssignmentContext"

const PremiumAssignmentManager: FC = () => {
  const { isPremiumUser, release, assignmentResult, error } =
    usePremiumAssignment()

  // Use ref to store the release function to avoid dependency issues
  const releaseRef = useRef(release)
  releaseRef.current = release

  // Handle cleanup on component unmount (app close/logout)
  useEffect(() => {
    return () => {
      if (isPremiumUser) {
        // Don't await this - just fire and forget on unmount
        releaseRef.current()
      }
    }
  }, [isPremiumUser]) // Only depend on isPremiumUser, not the release function

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
