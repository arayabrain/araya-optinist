import { UserListDTO, UserDTO } from "api/users/UsersApiDTO"

export const USER_SLICE_NAME = "user"

export type User = {
  currentUser?: UserDTO
  listUserSearch?: UserDTO[]
  loading: boolean
  listUser?: UserListDTO
  // Increments on each logout to help components detect stale closures
  logoutGeneration: number
}
