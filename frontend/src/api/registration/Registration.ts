import axios from "axios"
import {
  createUserWithEmailAndPassword,
  sendEmailVerification,
  updateProfile,
  signOut,
} from "firebase/auth"

import type {
  UserRegistrationRequestDTO,
  UserRegistrationResponseDTO,
  VerificationStatusDTO,
} from "api/registration/RegistrationApiDTO"
import { auth } from "config/firebase"

// Backend base URL
const API_BASE_URL = process.env.REACT_APP_SERVER_PROTO
  ? `${process.env.REACT_APP_SERVER_PROTO}://${process.env.REACT_APP_SERVER_HOST || "localhost"}:${process.env.REACT_APP_SERVER_PORT || 8000}`
  : "http://localhost:8000"

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
      await new Promise((resolve) => setTimeout(resolve, 500))
    }

    // ===================================
    // 1. Create Firebase user
    // ===================================

    const userCredential = await createUserWithEmailAndPassword(
      auth,
      data.email,
      data.password,
    )

    const firebaseUser = userCredential.user
    // ===================================
    // 2. Set display name
    // ===================================

    await updateProfile(firebaseUser, {
      displayName: data.name,
    })
    // ===================================
    // 3. Send verification email
    // ===================================
    await sendEmailVerification(firebaseUser, {
      url: `${window.location.origin}/login`,
      handleCodeInApp: false,
    })

    // ===================================
    // 4. Get ID token from new user
    // ===================================
    // Ensure we get the token from the new user
    const idToken = await firebaseUser.getIdToken(true)

    // ===================================
    // 5. Save to backend database
    // ===================================
    // Use unauthenticated axios instance
    const unauthAxios = createUnauthenticatedAxios()

    const response = await unauthAxios.post<UserRegistrationResponseDTO>(
      "/api/register/complete",
      {
        firebase_uid: firebaseUser.uid,
        email: firebaseUser.email,
        name: data.name,
        organization_id: data.organization_id || 1,
        role_id: data.role_id,
      },
      {
        headers: {
          Authorization: `Bearer ${idToken}`, // Explicitly set new token
        },
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
  const currentUser = auth.currentUser

  console.log("Resending verification email...")

  if (!currentUser) {
    throw new Error("User not found. Please log in again.")
  }

  if (currentUser.email !== email) {
    throw new Error("Email addresses do not match")
  }

  // Resend verification email from Firebase
  await sendEmailVerification(currentUser, {
    url: `${window.location.origin}/login`,
    handleCodeInApp: false,
  })

  console.log("Verification email resent!")

  return {
    success: true,
    message: "Verification email has been resent",
  }
}
