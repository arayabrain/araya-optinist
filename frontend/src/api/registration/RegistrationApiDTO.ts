export interface UserRegistrationRequestDTO {
  email: string
  password: string
  name: string
  organization_id?: number
  role_id?: number
}

export interface UserRegistrationResponseDTO {
  success: boolean
  message: string
  user: {
    id: number
    email: string
    name: string
    uid: string
    firebase_uid: string
    master_key: string
    email_verified: boolean
  }
}

export interface VerificationStatusDTO {
  email_verified: boolean
  uid: string
}
