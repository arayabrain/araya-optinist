export interface UserRegistrationRequestDTO {
  email: string
  password: string
  name: string
  organization_id?: number
  role_id?: number
}

export interface UserRegistrationResponseDTO {
  user: {
    id: number
    email: string
    name: string
    uid: string
    organization: {
      id: number
      name: string
    }
    role_id?: number
    data_usage?: number
    attributes?: Record<string, unknown>
  }
}

export interface VerificationStatusDTO {
  email_verified: boolean
  uid: string
}
