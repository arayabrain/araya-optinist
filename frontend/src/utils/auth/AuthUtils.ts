import { routingService } from "utils/routing/RoutingService"

export const saveToken = (access_token: string) => {
  localStorage.setItem("access_token", access_token)
}

export const getToken = () => {
  return localStorage.getItem("access_token")
}

export const saveRefreshToken = (refresh_token: string) => {
  localStorage.setItem("refresh_token", refresh_token)
}

export const getRefreshToken = () => {
  return localStorage.getItem("refresh_token")
}

export const removeRefreshToken = () => {
  return localStorage.removeItem("refresh_token")
}

export const logout = () => {
  removeRefreshToken()
  removeToken()
  removeExToken()
  // Clear dismissed warnings so they can appear again for the next user
  localStorage.removeItem("dismissedWarnings")
  // Clear session storage to prevent stale state on browser back
  sessionStorage.removeItem("storage-refreshed-on-login")

  // Clear premium routing information on logout
  try {
    routingService.clearRoutingInfo()
  } catch (e) {
    // Ignore if routing service isn't available
  }

  window.location.href = "/login"
}

export const removeToken = () => {
  return localStorage.removeItem("access_token")
}

export const saveExToken = (ExToken: string) => {
  localStorage.setItem("ExToken", ExToken)
}

export const getExToken = () => {
  return localStorage.getItem("ExToken")
}

export const removeExToken = () => {
  return localStorage.removeItem("ExToken")
}

// Public routes that don't require authentication
const PUBLIC_ROUTES = [
  /^\/$/,
  /^\/public(\/.*)?$/,
  /^\/login$/,
  /^\/register$/,
  /^\/reset-password$/,
  /^\/account-deleted$/,
]

export const isPublicRoute = (pathname: string): boolean => {
  return PUBLIC_ROUTES.some((pattern) => pattern.test(pathname))
}

export const requiresAuth = (pathname: string): boolean => {
  return !isPublicRoute(pathname)
}
