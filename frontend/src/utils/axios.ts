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
})

axios.interceptors.request.use(
  async (config) => {
    // Add authentication headers
    config.headers!.Authorization = `Bearer ${getToken()}`
    const exToken = getExToken()
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

axios.interceptors.response.use(
  async (res) => res,
  async (error) => {
    if (error?.response?.status === 401) {
      try {
        const { access_token } = await refreshTokenApi()
        saveToken(access_token)
        error.config.headers.Authorization = `Bearer ${access_token}`
        return axiosLibrary(error.config)
      } catch (e) {
        if (axiosLibrary.isAxiosError(e) && e?.response?.status === 400) {
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
