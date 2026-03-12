import axiosLibrary, {
  AxiosError,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios"

import { refreshTokenApi } from "api/auth/Auth"
import { API_TIMEOUT, BASE_URL } from "const/API"
import { RoutingHeaders } from "const/Subscription"
import { getExToken, getToken, logout, saveToken } from "utils/auth/AuthUtils"
import {
  isDataviewPublicOutputsRequest,
  DATAVIEW_PUBLIC_REQUEST_KEY,
} from "utils/DataviewUtils"
import { routingService } from "utils/routing/RoutingService"

// Extend AxiosRequestConfig to include custom retry property
interface CustomAxiosRequestConfig extends InternalAxiosRequestConfig {
  _retry?: boolean
  _retryWithoutPremium?: boolean
}

const axios = axiosLibrary.create({
  baseURL: BASE_URL,
  timeout: API_TIMEOUT.DEFAULT,
  headers: {
    Accept: "application/json",
    "Content-Type": "application/json",
  },
})

axios.interceptors.request.use(
  async (config) => {
    // Add authentication headers (skip if null to avoid "Bearer null")
    const token = getToken()
    const exToken = getExToken()

    if (token) {
      config.headers!.Authorization = `Bearer ${token}`
    }
    if (exToken) {
      config.headers!.ExToken = exToken
    }

    // Add premium routing headers for ALB-based routing
    // Skip if this is a free-tier fallback retry
    if (!(config as CustomAxiosRequestConfig)._retryWithoutPremium) {
      const routingHeaders = routingService.getRoutingHeaders()
      Object.assign(config.headers!, routingHeaders)
    }

    // Check whether the access is to public output data (HTTP header setting)
    if (config.url && isDataviewPublicOutputsRequest(config.url)) {
      config.headers![DATAVIEW_PUBLIC_REQUEST_KEY] = "true"
    }

    return config
  },
  (error) => Promise.reject(error),
)

// Track if we're already refreshing to prevent multiple refresh attempts
let isRefreshing = false
// Track if user is logging out to prevent token refresh during logout
let isLoggingOut = false
// Promise to track when logout completes
let logoutCompletePromise: Promise<void> | null = null
let resolveLogoutComplete: (() => void) | null = null

let failedQueue: Array<{
  resolve: (value?: unknown) => void
  reject: (reason?: unknown) => void
}> = []

const processQueue = (error: unknown, token: string | null = null) => {
  failedQueue.forEach((prom) => {
    if (error) {
      prom.reject(error)
    } else {
      prom.resolve(token)
    }
  })
  failedQueue = []
}

// Export function to set logout state - called by logout functions
export const setLoggingOut = (value: boolean) => {
  isLoggingOut = value
  if (value) {
    // Create a promise that will resolve when logout completes
    logoutCompletePromise = new Promise<void>((resolve) => {
      resolveLogoutComplete = resolve
    })
    // Clear the refresh queue when logging out
    processQueue(new Error("User is logging out"), null)
    isRefreshing = false
  } else {
    // Logout complete - resolve the promise
    if (resolveLogoutComplete) {
      resolveLogoutComplete()
      resolveLogoutComplete = null
      logoutCompletePromise = null
    }
  }
}

// Export function to wait for logout to complete
export const waitForLogoutComplete = async () => {
  if (logoutCompletePromise) {
    await logoutCompletePromise
  }
}

/**
 * Handle 401 Unauthorized errors by refreshing the access token and retrying the request
 */
const handleUnauthorizedError = async (
  error: AxiosError,
): Promise<AxiosResponse> => {
  // Guard: originalRequest must exist to proceed
  if (!error.config) {
    return Promise.reject(error)
  }

  const originalRequest = error.config as CustomAxiosRequestConfig

  // eslint-disable-next-line no-console
  console.error(
    "401 error detected:",
    originalRequest.url,
    error.response?.data,
  )

  // Prevent token refresh during logout
  if (isLoggingOut) {
    return Promise.reject(error)
  }

  // Prevent refresh loop - don't retry refresh endpoint itself
  if (originalRequest.url?.includes("/auth/refresh")) {
    // eslint-disable-next-line no-console
    console.error("Refresh token is invalid or expired, logging out")
    logout()
    return Promise.reject(error)
  }

  // Prevent infinite retry loops
  if (originalRequest._retry) {
    // eslint-disable-next-line no-console
    console.error("Token refresh retry failed, logging out")
    logout()
    return Promise.reject(error)
  }

  if (isRefreshing) {
    return new Promise((resolve, reject) => {
      failedQueue.push({ resolve, reject })
    })
      .then((token) => {
        originalRequest.headers.Authorization = `Bearer ${token}`
        return axiosLibrary(originalRequest)
      })
      .catch((err) => {
        return Promise.reject(err)
      })
  }

  originalRequest._retry = true
  isRefreshing = true

  try {
    const { access_token } = await refreshTokenApi()
    saveToken(access_token)
    originalRequest.headers.Authorization = `Bearer ${access_token}`

    processQueue(null, access_token)
    isRefreshing = false

    return axiosLibrary(originalRequest)
  } catch (e) {
    // eslint-disable-next-line no-console
    console.error("Token refresh failed:", e)
    processQueue(e, null)
    isRefreshing = false

    if (
      axiosLibrary.isAxiosError(e) &&
      (e?.response?.status === 400 || e?.response?.status === 401)
    ) {
      // eslint-disable-next-line no-console
      console.error("Invalid refresh token, logging out")
      logout()
    }
    throw e
  }
}

/**
 * Handle 503 Service Unavailable errors for premium routing by falling back to free tier
 */
const handlePremiumRoutingError = async (
  error: AxiosError,
): Promise<AxiosResponse> => {
  // Guard: originalRequest must exist to proceed
  if (!error.config) {
    return Promise.reject(error)
  }

  const originalRequest = error.config as CustomAxiosRequestConfig

  // Prevent infinite retry loops
  if (originalRequest._retryWithoutPremium) {
    return Promise.reject(error)
  }

  // Premium instance not ready, falling back to free tier.
  const retryConfig = { ...originalRequest }
  delete retryConfig.headers[RoutingHeaders.USER_TIER]
  delete retryConfig.headers[RoutingHeaders.ROUTING_ID]
  retryConfig._retryWithoutPremium = true

  try {
    // eslint-disable-next-line no-console
    console.log("Using free tier while premium instance provisions")
    return await axios(retryConfig)
  } catch (retryError) {
    // eslint-disable-next-line no-console
    console.error("Free tier fallback also failed:", retryError)
    return Promise.reject(error)
  }
}

axios.interceptors.response.use(
  async (res) => {
    // Extract routing headers from backend response
    // Note: axios normalizes response header names to lowercase
    const routingIdHeader = RoutingHeaders.ROUTING_ID.toLowerCase()
    const routingId = res.headers[routingIdHeader]
    if (routingId) {
      routingService.updateRoutingToken(routingId)
    }
    return res
  },
  async (error) => {
    if (error?.response?.status === 401) {
      return handleUnauthorizedError(error)
    }

    // ALB 503 when premium instance is unavailable.
    // Also handle network errors (ERR_FAILED / no response)
    const is503 = error?.response?.status === 503
    const isNetworkError = !error?.response && !!error?.config
    if ((is503 || isNetworkError) && routingService.requiresPremiumRouting()) {
      return handlePremiumRoutingError(error)
    }

    return Promise.reject(error)
  },
)

export default axios
