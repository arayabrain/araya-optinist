export type UserDTO = {
  uid?: string
  email: string
  id?: number
  data_usage: number
  name?: string
  organization?: {
    id: number
    name: string
  }
  role_id?: number
  attributes?: {
    remote_bucket_name?: string
  }
  subscription_plan_name?: string
  subscription_status?: string
  subscription_days_remaining?: number
  storage_usage_bytes?: number
  storage_quota_bytes?: number
  storage_usage_percentage?: number
  created_at?: string
  updated_at?: string
}

export type AddUserDTO = {
  email: string
  password: string
  name: string
  role_id: number
}

export type ListUsersQueryDTO = {
  name?: string
  email?: string
  sort?: string[]
  offset?: number
  limit?: number
}

export type UserListDTO = {
  items: UserDTO[]
  total: number
  limit: number
  offset: number
}

export type UpdateUserDTO = {
  role_id?: number
  name: string
  email: string
}

export type UpdateUserPasswordDTO = {
  old_password: string
  new_password: string
}
