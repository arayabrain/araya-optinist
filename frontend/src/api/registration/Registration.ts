import axios from "axios"

import type {
  UserRegistrationRequestDTO,
  UserRegistrationResponseDTO,
  VerificationStatusDTO,
} from "api/registration/RegistrationApiDTO"

// Constants
const API_BASE_URL = process.env.REACT_APP_SERVER_PROTO
  ? `${process.env.REACT_APP_SERVER_PROTO}://${process.env.REACT_APP_SERVER_HOST || "localhost"}:${process.env.REACT_APP_SERVER_PORT || 8000}`
  : "http://localhost:8000"

// User roles enum (matches backend UserRole)
export enum UserRole {
  ADMIN = 1,
  OPERATOR = 20,
}

/**
 * Create an unauthenticated axios instance
 * Do not use existing auth tokens during registration
 */
const createUnauthenticatedAxios = () => {
  return axios.create({
    baseURL: API_BASE_URL,
    headers: {
      "Content-Type": "application/json",
    },
  })
}

/**
 * User registration API
 */
export const registerUserApi = async (
  data: UserRegistrationRequestDTO,
): Promise<UserRegistrationResponseDTO> => {
  try {
    // ===================================
    // Call backend to create user and send verification email
    // ===================================
    // Backend will:
    // 1. Create Firebase user with email_verified=false
    // 2. Create database user record
    // 3. Send verification email automatically
    const unauthAxios = createUnauthenticatedAxios()

    const response = await unauthAxios.post<UserRegistrationResponseDTO>(
      "/api/register",
      {
        email: data.email,
        password: data.password,
        name: data.name,
        role_id: data.role_id || UserRole.OPERATOR, // Default to OPERATOR role
      },
    )

    return response.data
  } catch (error: unknown) {
    console.error("Registration error:", error)

    const err = error as {
      code?: string
      message?: string
      response?: { data?: { detail?: string }; status?: number }
    }

    console.error("Error details:", {
      message: err.message,
      response: err.response?.data,
      status: err.response?.status,
    })

    // Handle backend errors
    if (err.response?.data?.detail) {
      throw new Error(err.response.data.detail)
    }

    throw error
  }
}

/**
 * Check email verification status
 */
export const checkVerificationStatusApi = async (
  email: string,
): Promise<VerificationStatusDTO> => {
  const unauthAxios = createUnauthenticatedAxios()
  const response = await unauthAxios.get(
    `/api/register/verify-status/${encodeURIComponent(email)}`,
  )
  return response.data
}

/**
 * Resend verification email
 */
export const resendVerificationEmailApi = async (
  email: string,
): Promise<{ success: boolean; message: string }> => {
  try {
    console.log("Resending verification email...")

    // ===================================
    // Call backend to resend verification email
    // ===================================
    // Backend will send the verification email directly
    const unauthAxios = createUnauthenticatedAxios()
    const response = await unauthAxios.post<{
      success: boolean
      message: string
      already_verified: boolean
    }>("/api/register/resend-verification", { email })

    // If email is already verified, return success
    if (response.data.already_verified) {
      return {
        success: true,
        message: "Email is already verified",
      }
    }

    console.log("Verification email resent!")

    return {
      success: true,
      message: response.data.message || "Verification email has been resent",
    }
  } catch (error: unknown) {
    console.error("Failed to resend verification email:", error)

    const err = error as {
      code?: string
      message?: string
      response?: { data?: { detail?: string }; status?: number }
    }

    // Handle backend errors
    if (err.response?.data?.detail) {
      throw new Error(err.response.data.detail)
    }

    throw error
  }
}
