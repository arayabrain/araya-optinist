import axios from "axios"
import {
  signInWithCustomToken,
  sendEmailVerification,
  signOut,
} from "firebase/auth"

import type {
  UserRegistrationRequestDTO,
  UserRegistrationResponseDTO,
  VerificationStatusDTO,
} from "api/registration/RegistrationApiDTO"
import { auth } from "config/firebase"

// Constants
const API_BASE_URL = process.env.REACT_APP_SERVER_PROTO
  ? `${process.env.REACT_APP_SERVER_PROTO}://${process.env.REACT_APP_SERVER_HOST || "localhost"}:${process.env.REACT_APP_SERVER_PORT || 8000}`
  : "http://localhost:8000"

const SIGN_OUT_DELAY_MS = 500

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
    // 0. Clear existing session
    // ===================================

    if (auth.currentUser) {
      await signOut(auth)
      // Wait a moment after sign out
      await new Promise((resolve) => setTimeout(resolve, SIGN_OUT_DELAY_MS))
    }

    // ===================================
    // 1. Call backend to create user in database
    // ===================================
    // Backend will create both Firebase user and database user
    // and return a custom token for authentication
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

    const { custom_token } = response.data

    // ===================================
    // 2. Sign in with custom token from backend
    // ===================================
    const userCredential = await signInWithCustomToken(auth, custom_token)

    // ===================================
    // 3. Send verification email using Firebase
    // ===================================
    await sendEmailVerification(userCredential.user, {
      url: `${window.location.origin}/login`,
      handleCodeInApp: false,
    })

    // ===================================
    // 4. Sign out user (until they verify email)
    // ===================================
    await signOut(auth)

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

    // Handle Firebase errors
    if (err.code) {
      switch (err.code) {
        case "auth/email-already-in-use":
          throw new Error("This email address is already registered")
        case "auth/invalid-email":
          throw new Error("Invalid email address")
        case "auth/operation-not-allowed":
          throw new Error("Email/password authentication is not enabled")
        case "auth/weak-password":
          throw new Error("Password is too weak (requires 6+ characters)")
        case "auth/invalid-custom-token":
          throw new Error("Invalid authentication token from server")
        default:
          throw new Error(`Firebase error: ${err.message}`)
      }
    }

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
    // 1. Request custom token from backend
    // ===================================
    const unauthAxios = createUnauthenticatedAxios()
    const response = await unauthAxios.post<{
      success: boolean
      message: string
      custom_token: string
      already_verified: boolean
    }>("/api/register/resend-verification", { email })

    // If email is already verified, return success
    if (response.data.already_verified) {
      return {
        success: true,
        message: "Email is already verified",
      }
    }

    // ===================================
    // 2. Temporarily sign in with custom token
    // ===================================
    const userCredential = await signInWithCustomToken(
      auth,
      response.data.custom_token,
    )

    // ===================================
    // 3. Send verification email
    // ===================================
    await sendEmailVerification(userCredential.user, {
      url: `${window.location.origin}/login`,
      handleCodeInApp: false,
    })

    // ===================================
    // 4. Sign out immediately
    // ===================================
    await signOut(auth)

    console.log("Verification email resent!")

    return {
      success: true,
      message: "Verification email has been resent",
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

    // Handle Firebase errors
    if (err.code) {
      switch (err.code) {
        case "auth/invalid-custom-token":
          throw new Error("Invalid authentication token from server")
        default:
          throw new Error(`Firebase error: ${err.message}`)
      }
    }

    throw error
  }
}
