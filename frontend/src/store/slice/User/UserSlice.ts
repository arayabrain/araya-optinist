import { createSlice, isAnyOf } from "@reduxjs/toolkit"

import {
  deleteMe,
  getListUser,
  getListUserSearch,
  getMe,
  login,
  updateMe,
  updateMePassword,
  deleteUser,
  createUser,
  updateUser,
  proxyLogin,
} from "store/slice/User/UserActions"
import { USER_SLICE_NAME, User } from "store/slice/User/UserType"
import {
  removeExToken,
  removeRefreshToken,
  removeToken,
  saveExToken,
  saveRefreshToken,
  saveToken,
} from "utils/auth/AuthUtils"
import { setLoggingOut } from "utils/axios"
import { routingService } from "utils/routing/RoutingService"

const initialState: User = {
  currentUser: undefined,
  listUserSearch: undefined,
  listUser: undefined,
  loading: false,
  logoutGeneration: 0,
}

export const userSlice = createSlice({
  name: USER_SLICE_NAME,
  initialState,
  reducers: {
    logout: (state) => {
      // Set logout flag to prevent token refresh during logout
      // Note: Flag is NOT reset here - must call setLoggingOut(false)
      // after navigation completes
      setLoggingOut(true)

      // Remove tokens synchronously first - this is the critical step
      removeToken()
      removeRefreshToken()
      removeExToken()

      // Clear dismissed alerts so they can appear again for the next user
      localStorage.removeItem("dismissedAlerts")
      localStorage.removeItem("storageAlertDismissed")
      // Clear session storage to prevent stale state on browser back
      sessionStorage.removeItem("storage-refreshed-on-login")
      // Clear premium routing information
      routingService.clearRoutingInfo()

      // NOTE: setLoggingOut(false) is intentionally NOT called here
      // The caller (Profile.tsx) must call it after navigation completes
      // to prevent stale API calls from attempting refresh

      // Increment logoutGeneration to help components detect stale closures
      return {
        ...initialState,
        logoutGeneration: state.logoutGeneration + 1,
      }
    },
    resetUserSearch: (state) => {
      state.listUserSearch = []
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(getMe.fulfilled, (state, action) => {
        state.currentUser = action.payload
        // Update routing information when user data is loaded
        if (action.payload) {
          routingService.updateRoutingInfo(action.payload)
        }
      })
      .addCase(getListUser.fulfilled, (state, action) => {
        state.listUser = action.payload
        state.loading = false
      })
      .addCase(getListUserSearch.fulfilled, (state, action) => {
        state.listUserSearch = action.payload
        state.loading = false
      })
      .addCase(getMe.rejected, (state) => {
        state.currentUser = undefined
        state.loading = false
      })
      .addMatcher(
        isAnyOf(login.fulfilled, proxyLogin.fulfilled),
        (_, action) => {
          saveToken(action.payload.access_token)
          saveRefreshToken(action.payload.refresh_token)
          saveExToken(action.payload.ex_token)
        },
      )
      .addMatcher(
        isAnyOf(
          getListUserSearch.rejected,
          createUser.rejected,
          getListUser.rejected,
          updateUser.rejected,
          updateMePassword.rejected,
          updateMePassword.fulfilled,
          deleteUser.fulfilled,
          deleteUser.rejected,
          deleteMe.rejected,
          deleteMe.fulfilled,
          updateMe.rejected,
          updateMe.fulfilled,
          createUser.fulfilled,
        ),
        (state) => {
          state.loading = false
        },
      )
      .addMatcher(
        isAnyOf(
          getListUser.pending,
          deleteUser.pending,
          createUser.pending,
          updateMe.pending,
          deleteMe.pending,
          updateUser.pending,
          getListUserSearch.pending,
          updateMePassword.pending,
        ),
        (state) => {
          state.loading = true
        },
      )
      .addMatcher(isAnyOf(login.rejected, deleteMe.fulfilled), () => {
        removeToken()
        removeRefreshToken()
        removeExToken()
        // Clear premium routing information on auth failure
        routingService.clearRoutingInfo()
        return initialState
      })
  },
})

export const { logout, resetUserSearch } = userSlice.actions
export default userSlice.reducer
