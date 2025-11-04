import { RootState } from "store/store"

// ========================================
// Registration Selectors
// ========================================

export const selectRegistrationLoading = (state: RootState) =>
  state.registration.registration.loading

export const selectRegistrationSuccess = (state: RootState) =>
  state.registration.registration.success

export const selectRegistrationError = (state: RootState) =>
  state.registration.registration.error

export const selectRegistrationUser = (state: RootState) =>
  state.registration.registration.user

export const selectRegistration = (state: RootState) =>
  state.registration.registration

// ========================================
// Verification Status Selectors
// ========================================

export const selectVerificationStatusLoading = (state: RootState) =>
  state.registration.verificationStatus.loading

export const selectVerificationStatusError = (state: RootState) =>
  state.registration.verificationStatus.error

export const selectVerificationStatusData = (state: RootState) =>
  state.registration.verificationStatus.data

export const selectVerificationStatus = (state: RootState) =>
  state.registration.verificationStatus

// ========================================
// Resend Email Selectors
// ========================================

export const selectResendEmailLoading = (state: RootState) =>
  state.registration.resendEmail.loading

export const selectResendEmailSuccess = (state: RootState) =>
  state.registration.resendEmail.success

export const selectResendEmailError = (state: RootState) =>
  state.registration.resendEmail.error

export const selectResendEmail = (state: RootState) =>
  state.registration.resendEmail

// ========================================
// Combined Selectors
// ========================================

export const selectIsAnyRegistrationLoading = (state: RootState) =>
  state.registration.registration.loading ||
  state.registration.verificationStatus.loading ||
  state.registration.resendEmail.loading

export const selectHasAnyRegistrationError = (state: RootState) =>
  !!state.registration.registration.error ||
  !!state.registration.verificationStatus.error ||
  !!state.registration.resendEmail.error
