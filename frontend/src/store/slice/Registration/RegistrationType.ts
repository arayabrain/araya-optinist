import type {
  UserRegistrationResponseDTO,
  VerificationStatusDTO,
} from "api/registration/RegistrationApiDTO"

export interface RegistrationState {
  // User Registration
  registration: {
    loading: boolean
    success: boolean
    error: string | null
    user: UserRegistrationResponseDTO["user"] | null
  }

  // Verification Status
  verificationStatus: {
    loading: boolean
    error: string | null
    data: VerificationStatusDTO | null
  }

  // Resend Email
  resendEmail: {
    loading: boolean
    success: boolean
    error: string | null
  }
}

// Re-export DTOs for convenience
export type {
  UserRegistrationRequestDTO,
  UserRegistrationResponseDTO,
  VerificationStatusDTO,
} from "api/registration/RegistrationApiDTO"
