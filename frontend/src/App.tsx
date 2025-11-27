import { FC, useEffect } from "react"
import { useDispatch, useSelector } from "react-redux"
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"

import { SnackbarProvider, SnackbarKey, useSnackbar } from "notistack"

import Close from "@mui/icons-material/Close"
import IconButton from "@mui/material/IconButton"

import Loading from "components/common/Loading"
import Layout from "components/Layout"
import { RETRY_WAIT } from "const/Mode"
import Account from "pages/Account"
import AccountDelete from "pages/AccountDelete"
import AccountManager from "pages/AccountManager"
import Dashboard from "pages/Dashboard"
import Dataview from "pages/Dataview"
import InvoicesPage from "pages/Invoice"
import Login from "pages/Login"
import PublicDataview from "pages/PublicDataview"
import RegistrationForm from "pages/Register/MainRegistration"
import ResetPassword from "pages/ResetPassword"
import SubscriptionPage from "pages/Subscription"
import Failed from "pages/Subscription/failed"
import Thanks from "pages/Subscription/thanks"
import Workspaces from "pages/Workspace"
import Workspace from "pages/Workspace/Workspace"
import { getModeStandalone } from "store/slice/Standalone/StandaloneActions"
import {
  selectLoading,
  selectModeStandalone,
} from "store/slice/Standalone/StandaloneSeclector"
import { AppDispatch } from "store/store"

const App: FC = () => {
  const dispatch = useDispatch<AppDispatch>()
  const isStandalone = useSelector(selectModeStandalone)
  const loading = useSelector(selectLoading)
  const getMode = () => {
    dispatch(getModeStandalone())
      .unwrap()
      .catch(() => {
        new Promise((resolve) => setTimeout(resolve, RETRY_WAIT)).then(() => {
          getMode()
        })
      })
  }

  useEffect(() => {
    getMode()
    //eslint-disable-next-line
  }, [])

  return loading ? (
    <Loading loading={true} />
  ) : (
    <SnackbarProvider
      maxSnack={5}
      action={(snackbarKey) => (
        <SnackbarCloseButton snackbarKey={snackbarKey} />
      )}
    >
      <BrowserRouter>
        <Layout>
          {isStandalone ? (
            <Routes>
              <Route path="/" element={<Workspace />} />
              <Route path="*" element={<Navigate replace to="/" />} />
            </Routes>
          ) : (
            <Routes>
              {/* Public routes */}
              <Route path="/" element={<Navigate replace to="/public" />} />
              <Route path="/public" element={<PublicDataview />} />
              <Route path="/account-deleted" element={<AccountDelete />} />
              <Route path="/login" element={<Login />} />
              <Route path="/register" element={<RegistrationForm />} />
              <Route path="/reset-password" element={<ResetPassword />} />

              {/* Authenticated routes */}
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/account" element={<Account />} />
              <Route path="/account-manager" element={<AccountManager />} />

              <Route path="/dataview">
                <Route path="" element={<Dataview />} />
                <Route path=":workspaceId" element={<Dataview />} />
              </Route>

              <Route path="/workspaces">
                <Route path="" element={<Workspaces />} />
                <Route path=":workspaceId" element={<Workspace />} />
              </Route>

              <Route path="/subscription/thanks" element={<Thanks />} />
              <Route path="/subscription/failed" element={<Failed />} />
              <Route path="/subscription" element={<SubscriptionPage />} />
              <Route path="/subscription/manage" element={<InvoicesPage />} />

              {/* Catch-all */}
              <Route path="*" element={<Navigate replace to="/" />} />
            </Routes>
          )}
        </Layout>
      </BrowserRouter>
    </SnackbarProvider>
  )
}

const SnackbarCloseButton: FC<{ snackbarKey: SnackbarKey }> = ({
  snackbarKey,
}) => {
  const { closeSnackbar } = useSnackbar()
  return (
    <IconButton onClick={() => closeSnackbar(snackbarKey)} size="large">
      <Close style={{ color: "white" }} />
    </IconButton>
  )
}

export default App
