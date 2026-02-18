/**
 * Premium Assignment Manager
 *
 * Component that handles cleanup and logging for premium assignments.
 * Assignment logic is handled by PremiumAssignmentContext.
 */

import { FC, useEffect } from "react"

import { usePremiumAssignment } from "contexts/PremiumAssignmentContext"

const PremiumAssignmentManager: FC = () => {
  const { assignmentResult, error } = usePremiumAssignment()

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
