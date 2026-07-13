import { createSlice } from "@reduxjs/toolkit"

import {
  registerUser,
  checkVerificationStatus,
  resendVerificationEmail,
} from "store/slice/Registration/RegistrationActions"
import type { RegistrationState } from "store/slice/Registration/RegistrationType"

const initialState: RegistrationState = {
  registration: {
    loading: false,
    success: false,
    error: null,
    user: null,
  },
  verificationStatus: {
    loading: false,
    error: null,
    data: null,
  },
  resendEmail: {
    loading: false,
    success: false,
    error: null,
  },
}

const registrationSlice = createSlice({
  name: "registration",
  initialState,
  reducers: {
    /**
     * すべての登録エラーをクリア
     */
    clearRegistrationErrors: (state) => {
      state.registration.error = null
      state.verificationStatus.error = null
      state.resendEmail.error = null
    },

    /**
     * 登録成功状態をクリア
     */
    clearRegistrationSuccess: (state) => {
      state.registration.success = false
    },

    /**
     * メール再送信成功状態をクリア
     */
    clearResendSuccess: (state) => {
      state.resendEmail.success = false
    },

    /**
     * すべての登録状態をリセット
     */
    clearAllRegistrationState: () => initialState,
  },
  extraReducers: (builder) => {
    // ========================================
    // User Registration
    // ========================================
    builder
      .addCase(registerUser.pending, (state) => {
        state.registration.loading = true
        state.registration.error = null
        state.registration.success = false
      })
      .addCase(registerUser.fulfilled, (state, action) => {
        state.registration.loading = false
        state.registration.success = true
        state.registration.user = action.payload.user
      })
      .addCase(registerUser.rejected, (state, action) => {
        state.registration.loading = false
        state.registration.error = action.payload || "エラーが発生しました"
        state.registration.success = false
      })

    // ========================================
    // Verification Status Check
    // ========================================
    builder
      .addCase(checkVerificationStatus.pending, (state) => {
        state.verificationStatus.loading = true
        state.verificationStatus.error = null
      })
      .addCase(checkVerificationStatus.fulfilled, (state, action) => {
        state.verificationStatus.loading = false
        state.verificationStatus.data = action.payload
      })
      .addCase(checkVerificationStatus.rejected, (state, action) => {
        state.verificationStatus.loading = false
        state.verificationStatus.error =
          action.payload || "エラーが発生しました"
      })

    // ========================================
    // Resend Verification Email
    // ========================================
    builder
      .addCase(resendVerificationEmail.pending, (state) => {
        state.resendEmail.loading = true
        state.resendEmail.error = null
        state.resendEmail.success = false
      })
      .addCase(resendVerificationEmail.fulfilled, (state) => {
        state.resendEmail.loading = false
        state.resendEmail.success = true
      })
      .addCase(resendVerificationEmail.rejected, (state, action) => {
        state.resendEmail.loading = false
        state.resendEmail.error = action.payload || "エラーが発生しました"
        state.resendEmail.success = false
      })
  },
})

export const {
  clearRegistrationErrors,
  clearRegistrationSuccess,
  clearResendSuccess,
  clearAllRegistrationState,
} = registrationSlice.actions

export default registrationSlice.reducer
