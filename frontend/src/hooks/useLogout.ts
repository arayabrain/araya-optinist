import { useCallback } from "react"
import { useDispatch } from "react-redux"
import { useNavigate } from "react-router-dom"

import { usePremiumAssignment } from "contexts/PremiumAssignmentContext"
import { logout } from "store/slice/User/UserSlice"
import { setLoggingOut } from "utils/axios"
import { tabSync } from "utils/crossTabSync"

export const useLogout = () => {
  const dispatch = useDispatch()
  const navigate = useNavigate()
  const { autoReleaseOnLogout, isPremiumUser } = usePremiumAssignment()

  const performLogout = useCallback(
    async (broadcast = true) => {
      // Fire-and-forget: don't block logout on Lambda release.
      // Only the originating tab releases; cross-tab receivers skip it.
      if (isPremiumUser && broadcast) {
        autoReleaseOnLogout().catch((error) => {
          // eslint-disable-next-line no-console
          console.warn("Failed to release premium instance:", error)
        })
      }

      if (broadcast) {
        tabSync.broadcastLogout()
      }

      dispatch(logout())
      navigate("/login")
      setLoggingOut(false)
    },
    [isPremiumUser, autoReleaseOnLogout, dispatch, navigate],
  )

  return { performLogout }
}
