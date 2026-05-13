import { useCallback } from "react"
import { useDispatch } from "react-redux"
import { useNavigate } from "react-router-dom"

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
