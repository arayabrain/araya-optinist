import { routingService } from "utils/routing/RoutingService"

// Import setLoggingOut from axios - using dynamic import to avoid circular dependency
let setLoggingOutFn: ((value: boolean) => void) | null = null

const getSetLoggingOut = async () => {
  if (!setLoggingOutFn) {
    const axiosModule = await import("utils/axios")
    setLoggingOutFn = axiosModule.setLoggingOut
  }
  return setLoggingOutFn
}

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

export const logout = async () => {
  // Set logout flag to prevent token refresh during logout
  const setLoggingOut = await getSetLoggingOut()
  setLoggingOut(true)

  // Call backend logout endpoint for free tier users (fire and forget)
  try {
    const { logoutFreeUserApi } = await import("api/users/UsersMe")
    await logoutFreeUserApi()
  } catch (e) {
    // Ignore errors - logout should proceed even if API call fails
  }

  // Remove tokens synchronously first - this is the critical step
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

  // Reset logout flag immediately after token removal
  // This ensures any pending checks see the cleared tokens
  setLoggingOut(false)

  // Navigate to login - this is safe now that tokens are removed and flag is reset
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
