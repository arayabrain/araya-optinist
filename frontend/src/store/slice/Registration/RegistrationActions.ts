import { createAsyncThunk } from "@reduxjs/toolkit"

import {
  registerUserApi,
  checkVerificationStatusApi,
  resendVerificationEmailApi,
} from "api/registration/Registration"
import type {
  UserRegistrationRequestDTO,
  UserRegistrationResponseDTO,
  VerificationStatusDTO,
} from "api/registration/RegistrationApiDTO"

/**
 * Type guard: Check if error is an Axios error
 */
interface AxiosError {
  response?: {
    data?: {
      detail?: string | Array<{ msg: string }>
    }
  }
  message?: string
  code?: string
}

const isAxiosError = (error: unknown): error is AxiosError => {
  return (
    typeof error === "object" &&
    error !== null &&
    ("response" in error || "message" in error || "code" in error)
  )
}

/**
 * Helper function to extract error messages
 */
const extractErrorMessage = (
  error: unknown,
  defaultMessage: string,
): string => {
  // For Axios errors
  if (isAxiosError(error)) {
    // Error response from server
    if (error.response?.data?.detail) {
      const detail = error.response.data.detail

      // For Pydantic validation errors (array)
      if (Array.isArray(detail)) {
        return detail.map((err) => err.msg).join(", ")
      }

      // For string errors
      if (typeof detail === "string") {
        return detail
      }
    }

    // For network errors
    if (error.message === "Network Error") {
      return "A network error occurred. Please check your internet connection."
    }

    // For timeout errors
    if (error.code === "ECONNABORTED") {
      return "The request timed out. Please try again."
    }

    // For other error messages
    if (error.message) {
      return error.message
    }
  }

  // For Error objects
  if (error instanceof Error) {
    return error.message
  }

  // For strings
  if (typeof error === "string") {
    return error
  }

  // For other cases, use default message
  return defaultMessage
}

/**
 * User registration action
 * Creates a user in Firebase and sends a verification email
 */
export const registerUser = createAsyncThunk<
  UserRegistrationResponseDTO,
  UserRegistrationRequestDTO,
  { rejectValue: string }
>("registration/registerUser", async (data, { rejectWithValue }) => {
  try {
    const response = await registerUserApi(data)
    return response
  } catch (error: unknown) {
    const errorMessage = extractErrorMessage(
      error,
      "Registration failed. Please try again.",
    )
    return rejectWithValue(errorMessage)
  }
})

/**
 * Email verification status check action
 */
export const checkVerificationStatus = createAsyncThunk<
  VerificationStatusDTO,
  string,
  { rejectValue: string }
>(
  "registration/checkVerificationStatus",
  async (email, { rejectWithValue }) => {
    try {
      const response = await checkVerificationStatusApi(email)
      return response
    } catch (error: unknown) {
      const errorMessage = extractErrorMessage(
        error,
        "Failed to check verification status",
      )
      return rejectWithValue(errorMessage)
    }
  },
)

/**
 * Resend verification email action
 */
export const resendVerificationEmail = createAsyncThunk<
  { success: boolean; message: string },
  string,
  { rejectValue: string }
>(
  "registration/resendVerificationEmail",
  async (email, { rejectWithValue }) => {
    try {
      const response = await resendVerificationEmailApi(email)
      return response
    } catch (error: unknown) {
      const errorMessage = extractErrorMessage(error, "Failed to send email")
      return rejectWithValue(errorMessage)
    }
  },
)
