import { useCallback } from "react"
import { useDispatch } from "react-redux"
import { useNavigate } from "react-router-dom"

import { logoutFreeUserApi } from "api/users/UsersMe"
import { usePremiumAssignment } from "contexts/PremiumAssignmentContext"
import { logout } from "store/slice/User/UserSlice"
import { setLoggingOut } from "utils/axios"
import { tabSync } from "utils/crossTabSync"
import { flushErrors } from "utils/errorReporter"

export const useLogout = () => {
  const dispatch = useDispatch()
  const navigate = useNavigate()
  const { autoReleaseOnLogout, isPremiumUser } = usePremiumAssignment()

  const performLogout = useCallback(
    async (broadcast = true) => {
      // Release via sendBeacon (uses HMAC token, not auth header)
      // so it's safe to call before dispatch(logout) clears creds.
      if (isPremiumUser && broadcast) {
        autoReleaseOnLogout()
      }

      // Notify backend for free tier users so it can record logged_out_at
      // and close the active instance_usage_log session.
      // Must run before dispatch(logout()) which clears auth tokens.
      // Only on the originating tab (broadcast=true) to avoid duplicate calls.
      if (!isPremiumUser && broadcast) {
        try {
          await logoutFreeUserApi()
        } catch {
          // Ignore errors - logout should proceed even if API call fails
        }
      }

      if (broadcast) {
        tabSync.broadcastLogout()
      }

      flushErrors()

      dispatch(logout())
      navigate("/login")
      setLoggingOut(false)
    },
    [isPremiumUser, autoReleaseOnLogout, dispatch, navigate],
  )

  return { performLogout }
}
