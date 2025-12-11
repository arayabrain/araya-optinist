import { FC, ReactNode, useEffect, useState } from "react"
import { useSelector, useDispatch } from "react-redux"
import { useLocation, useNavigate } from "react-router-dom"

import { Box } from "@mui/material"
import { styled } from "@mui/material/styles"

import LimitAlert from "components/common/LimitAlert"
import Loading from "components/common/Loading"
import { LogsFloatingButton } from "components/common/LogsFloatingButton"
import Header from "components/Layout/Header"
import LeftMenu from "components/Layout/LeftMenu"
import ModalLogs from "components/Workspace/FlowChart/ModalLogs"
import { APP_BAR_HEIGHT } from "const/Layout"
import { selectLogsModalIsOpen } from "store/slice/LogsModal/LogsModalSelectors"
import { closeLogsModal } from "store/slice/LogsModal/LogsModalSlice"
import { selectModeStandalone } from "store/slice/Standalone/StandaloneSeclector"
import { getMe } from "store/slice/User/UserActions"
import { selectCurrentUser } from "store/slice/User/UserSelector"
import { AppDispatch } from "store/store"
import { getToken, requiresAuth } from "utils/auth/AuthUtils"

const Layout = ({ children }: { children?: ReactNode }) => {
  const user = useSelector(selectCurrentUser)
  const location = useLocation()
  const navigate = useNavigate()
  const dispatch = useDispatch<AppDispatch>()
  const isStandalone = useSelector(selectModeStandalone)

  const [loading, setLoading] = useState(
    !isStandalone && requiresAuth(location.pathname),
  )
  const [storageRefreshedOnLogin, setStorageRefreshedOnLogin] = useState(() => {
    // Check if storage was already refreshed in this session
    return sessionStorage.getItem("storage-refreshed-on-login") === "true"
  })

  useEffect(() => {
    if (!isStandalone) {
      if (requiresAuth(location.pathname)) {
        checkAuth()
      } else {
        // For public routes, check if logged-in user should be redirected
        checkPublicRouteAccess()
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname, user])

  const checkPublicRouteAccess = async () => {
    const isAuthPage = ["/login", "/register"].includes(location.pathname)
    const token = getToken()

    // For all public routes, if there's a token, fetch user data to show correct header
    if (!user) {
      if (token) {
        try {
          await dispatch(getMe())
          // Revalidate token after async operation - logout may have occurred during getMe()
          const currentToken = getToken()
          if (!currentToken) {
            // Token was removed during getMe(), user logged out - stay on current page
            return
          }
          // If on login/register page and successfully authenticated, redirect to dashboard
          if (isAuthPage) {
            navigate("/dashboard", { replace: true })
          }
        } catch {
          // Invalid token, stay on current page
        }
      }
    } else if (isAuthPage) {
      // If user exists in Redux but no token, this is a logout race condition
      // Stay on auth page and let Redux state clear
      if (!token) {
        return
      }
      // Revalidate token before navigation - user might have logged out
      const currentToken = getToken()
      if (!currentToken) {
        // Token was removed, don't navigate
        return
      }
      // If user is already logged in and trying to access login/register, redirect to dashboard
      navigate("/dashboard", { replace: true })
    }
  }

  const checkAuth = async () => {
    const token = getToken()
    const isLogin = location.pathname === "/login"

    // Always check token first, even if Redux has user data
    // This prevents logout issues where user navigates to protected pages after logout
    if (!token) {
      // No token means user is logged out, clear any stale Redux state
      if (user) {
        // Token was removed but Redux state hasn't cleared yet
        // Force navigation to login to trigger cleanup
        navigate("/login", { replace: true })
        if (loading) setLoading(false)
        return
      }
      if (!isLogin) {
        navigate("/login", { replace: true })
      }
      if (loading) setLoading(false)
      return
    }

    // If we have a token and user data, auth is valid
    // But verify they're in sync - if we have user but no token, this is a logout race condition
    if (user && token) {
      if (loading) setLoading(false)
      return
    }

    // Have token but no user data - fetch user
    try {
      await dispatch(getMe())

      // Revalidate token after getMe() - logout may have occurred during async operation
      let currentToken = getToken()
      if (!currentToken) {
        // Token was removed during getMe(), user logged out
        navigate("/login", { replace: true })
        if (loading) setLoading(false)
        return
      }

      // Refresh workspace storage only once per session to ensure accurate limit warnings
      if (!storageRefreshedOnLogin) {
        try {
          const { refreshAllWorkspacesStorageApi } = await import(
            "api/workspace"
          )
          await refreshAllWorkspacesStorageApi()

          // Revalidate token after storage refresh - logout may have occurred
          currentToken = getToken()
          if (!currentToken) {
            navigate("/login", { replace: true })
            if (loading) setLoading(false)
            return
          }

          // Mark as refreshed in session storage
          sessionStorage.setItem("storage-refreshed-on-login", "true")
          setStorageRefreshedOnLogin(true)
        } catch (storageError) {
          // Don't fail login if storage refresh fails
          // eslint-disable-next-line no-console
          console.warn(
            "Failed to refresh workspace storage usage on login:",
            storageError,
          )

          // Still mark as attempted so we don't keep retrying
          sessionStorage.setItem("storage-refreshed-on-login", "true")
          setStorageRefreshedOnLogin(true)
        }
      }

      // Final token revalidation before navigation to authorized page
      currentToken = getToken()
      if (!currentToken) {
        // Token was removed, user logged out - redirect to login
        navigate("/login", { replace: true })
        if (loading) setLoading(false)
        return
      }

      if (isLogin) navigate("/dashboard")
    } catch {
      // Token is invalid or getMe failed - clear auth and redirect
      navigate("/login", { replace: true })
    } finally {
      if (loading) setLoading(false)
    }
  }

  if (isStandalone) {
    return <AuthedLayout>{children}</AuthedLayout>
  }

  if (requiresAuth(location.pathname)) {
    if (loading) {
      return <Loading loading={true} />
    }
    return <AuthedLayout>{children}</AuthedLayout>
  }

  return <UnauthedLayout>{children}</UnauthedLayout>
}

const AuthedLayout: FC<{ children: ReactNode }> = ({ children }) => {
  const dispatch = useDispatch<AppDispatch>()
  const [open, setOpen] = useState(false)
  const logsModalOpen = useSelector(selectLogsModalIsOpen)
  const isStandalone = useSelector(selectModeStandalone)

  const handleDrawerOpen = () => {
    setOpen(true)
  }

  const handleDrawerClose = () => {
    setOpen(false)
  }

  const handleLogsModalClose = () => {
    dispatch(closeLogsModal())
  }

  return (
    <LayoutWrapper>
      <Header handleDrawerOpen={handleDrawerOpen} />
      <ContentBodyWrapper>
        <LeftMenu open={open} handleDrawerClose={handleDrawerClose} />
        <ChildrenWrapper>{children}</ChildrenWrapper>
      </ContentBodyWrapper>
      {/* Global limit alert modal for authenticated users */}
      <LimitAlert showAsModal={true} autoCheck={true} />
      {!isStandalone && <LogsFloatingButton />}
      {logsModalOpen && <ModalLogs isOpen onClose={handleLogsModalClose} />}
    </LayoutWrapper>
  )
}

const UnauthedLayout: FC<{ children: ReactNode }> = ({ children }) => {
  return (
    <LayoutWrapper>
      <ContentBodyWrapper>
        <ChildrenWrapper>{children}</ChildrenWrapper>
      </ContentBodyWrapper>
    </LayoutWrapper>
  )
}

const LayoutWrapper = styled(Box)({
  height: "100%",
  width: "100%",
})

const ContentBodyWrapper = styled(Box)(() => ({
  backgroundColor: "#ffffff",
  display: "flex",
  paddingTop: APP_BAR_HEIGHT,
  height: `calc(100% - ${APP_BAR_HEIGHT}px)`,
  paddingRight: 10,
  overflow: "auto",
}))

const ChildrenWrapper = styled("main", {
  shouldForwardProp: (prop) => prop !== "open",
})(({ theme }) => ({
  flexGrow: 1,
  padding: theme.spacing(3),
  transition: theme.transitions.create("margin", {
    easing: theme.transitions.easing.sharp,
    duration: theme.transitions.duration.leavingScreen,
  }),
}))

export default Layout
