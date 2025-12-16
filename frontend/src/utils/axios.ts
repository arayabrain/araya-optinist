import axiosLibrary from "axios"

import { refreshTokenApi } from "api/auth/Auth"
import { BASE_URL } from "const/API"
import { getExToken, getToken, logout, saveToken } from "utils/auth/AuthUtils"
import {
  isDataviewPublicOutputsRequest,
  DATAVIEW_PUBLIC_REQUEST_KEY,
} from "utils/DataviewUtils"
import { routingService } from "utils/routing/RoutingService"

const axios = axiosLibrary.create({
  baseURL: BASE_URL,
  timeout: 600000,
  headers: {
    Accept: "application/json",
    "Content-Type": "application/json",
  },
})

axios.interceptors.request.use(
  async (config) => {
    // Add authentication headers
    const token = getToken()
    const exToken = getExToken()

    config.headers!.Authorization = `Bearer ${token}`
    if (exToken) {
      config.headers!.ExToken = exToken
    }

    // Add premium routing headers for ALB-based routing
    const routingHeaders = routingService.getRoutingHeaders()
    Object.assign(config.headers!, routingHeaders)

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

axios.interceptors.response.use(
  async (res) => res,
  async (error) => {
    const originalRequest = error.config

    if (error?.response?.status === 401) {
      // eslint-disable-next-line no-console
      console.error(
        "401 error detected:",
        originalRequest?.url,
        error.response?.data,
      )

      // Prevent token refresh during logout
      if (isLoggingOut) {
        return Promise.reject(error)
      }

      // Prevent refresh loop - don't retry refresh endpoint itself
      if (originalRequest?.url?.includes("/auth/refresh")) {
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

    // Handle premium routing failures gracefully
    if (
      error?.response?.status === 503 &&
      routingService.requiresPremiumRouting()
    ) {
      // Premium instance not ready, falling back to free tier until migration

      // Retry request without premium headers to use free tier
      if (error.config && !error.config._retryWithoutPremium) {
        const retryConfig = { ...error.config }

        // Remove premium routing headers for free tier fallback
        delete retryConfig.headers["X-User-Tier"]
        delete retryConfig.headers["X-User-ID"]

        // Mark as retry to prevent infinite loops
        retryConfig._retryWithoutPremium = true

        try {
          // eslint-disable-next-line no-console
          console.log("Using free tier while premium instance provisions")
          return await axiosLibrary(retryConfig)
        } catch (retryError) {
          // eslint-disable-next-line no-console
          console.error("Free tier fallback also failed:", retryError)
          // Let the original error bubble up
        }
      }
    }

    return Promise.reject(error)
  },
)

export default axios
