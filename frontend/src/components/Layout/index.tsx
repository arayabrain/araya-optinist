import { FC, ReactNode, useEffect, useState } from "react"
import { useSelector, useDispatch } from "react-redux"
import { useLocation, useNavigate } from "react-router-dom"

import { Box } from "@mui/material"
import { styled } from "@mui/material/styles"

import LimitWarning from "components/common/LimitWarning"
import Loading from "components/common/Loading"
import Header from "components/Layout/Header"
import LeftMenu from "components/Layout/LeftMenu"
import { APP_BAR_HEIGHT } from "const/Layout"
import { selectModeStandalone } from "store/slice/Standalone/StandaloneSeclector"
import { getMe } from "store/slice/User/UserActions"
import { selectCurrentUser } from "store/slice/User/UserSelector"
import { AppDispatch } from "store/store"
import { getToken } from "utils/auth/AuthUtils"

const authRequiredPathRegex = /^\/console\/?.*/

const Layout = ({ children }: { children?: ReactNode }) => {
  const user = useSelector(selectCurrentUser)
  const location = useLocation()
  const navigate = useNavigate()
  const dispatch = useDispatch<AppDispatch>()
  const isStandalone = useSelector(selectModeStandalone)

  const [loading, setLoading] = useState(
    !isStandalone && authRequiredPathRegex.test(location.pathname),
  )
  const [storageRefreshedOnLogin, setStorageRefreshedOnLogin] = useState(() => {
    // Check if storage was already refreshed in this session
    return sessionStorage.getItem("storage-refreshed-on-login") === "true"
  })

  useEffect(() => {
    !isStandalone &&
      authRequiredPathRegex.test(location.pathname) &&
      checkAuth()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname, user])

  const checkAuth = async () => {
    if (user) {
      if (loading) setLoading(false)
      return
    }
    const token = getToken()
    const isLogin = location.pathname === "/login"

    try {
      if (token) {
        await dispatch(getMe())

        // Refresh workspace storage only once per session to ensure accurate limit warnings
        if (!storageRefreshedOnLogin) {
          try {
            const { refreshAllWorkspacesStorageApi } = await import(
              "api/workspace"
            )
            await refreshAllWorkspacesStorageApi()

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

        if (isLogin) navigate("/console")
        return
      } else if (!isLogin) throw new Error("fail auth")
    } catch {
      navigate("/login", { replace: true })
    } finally {
      if (loading) setLoading(false)
    }
  }

  return isStandalone || authRequiredPathRegex.test(location.pathname) ? (
    <AuthedLayout>{children}</AuthedLayout>
  ) : (
    <>
      <Loading loading={loading} />
      <UnauthedLayout>{children}</UnauthedLayout>
    </>
  )
}

const AuthedLayout: FC<{ children: ReactNode }> = ({ children }) => {
  const [open, setOpen] = useState(false)
  const handleDrawerOpen = () => {
    setOpen(true)
  }

  const handleDrawerClose = () => {
    setOpen(false)
  }
  return (
    <LayoutWrapper>
      <Header handleDrawerOpen={handleDrawerOpen} />
      <ContentBodyWrapper>
        <LeftMenu open={open} handleDrawerClose={handleDrawerClose} />
        <ChildrenWrapper>{children}</ChildrenWrapper>
      </ContentBodyWrapper>
      {/* Global limit warning modal for authenticated users */}
      <LimitWarning showAsModal={true} autoCheck={true} />
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
