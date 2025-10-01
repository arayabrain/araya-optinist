/**
 * Inactivity Warning Component
 *
 * Shows a warning snackbar when premium users have been inactive for 1 hour.
 * Warns that their instance will be released after another hour of inactivity.
 */

import React, { useEffect, useState } from "react"

import { Alert, Button, Snackbar } from "@mui/material"

import { usePremiumAssignment } from "contexts/PremiumAssignmentContext"

const InactivityWarning: React.FC = () => {
  const { showInactivityWarning, dismissInactivityWarning, recordActivity } =
    usePremiumAssignment()
  const [countdown, setCountdown] = useState(60) // 60 minutes countdown

  // Countdown timer for the warning
  useEffect(() => {
    if (!showInactivityWarning) {
      setCountdown(60) // Reset countdown when warning is dismissed
      return
    }

    const countdownInterval = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          // Time's up - the context should handle auto-release
          return 0
        }
        return prev - 1
      })
    }, 60 * 1000) // Update every minute

    return () => clearInterval(countdownInterval)
  }, [showInactivityWarning])

  const handleStayActive = () => {
    // Record activity and dismiss warning
    recordActivity().catch((error) => {
      // eslint-disable-next-line no-console
      console.warn("Failed to record activity:", error)
    })
    dismissInactivityWarning()
  }

  const formatTime = (minutes: number) => {
    const hours = Math.floor(minutes / 60)
    const mins = minutes % 60
    if (hours > 0) {
      return `${hours}h ${mins}m`
    }
    return `${mins}m`
  }

  return (
    <Snackbar
      open={showInactivityWarning}
      anchorOrigin={{ vertical: "top", horizontal: "center" }}
      // Don't auto-hide - user must interact
    >
      <Alert
        severity="warning"
        variant="filled"
        action={
          <Button
            color="inherit"
            size="small"
            onClick={handleStayActive}
            sx={{ fontWeight: "bold" }}
          >
            Stay Active
          </Button>
        }
        sx={{
          minWidth: "400px",
          "& .MuiAlert-message": {
            fontSize: "14px",
            lineHeight: 1.4,
          },
        }}
      >
        <strong>Premium Instance Inactivity Warning</strong>
        <br />
        You&apos;ve been inactive for 1 hour. Your premium instance will be
        automatically released in {formatTime(countdown)} if no activity is
        detected.
        <br />
        <small>
          Click &quot;Stay Active&quot; or interact with the page to keep your
          instance active.
        </small>
      </Alert>
    </Snackbar>
  )
}

export default InactivityWarning
