import axiosLibrary from "axios"

import { refreshTokenApi } from "api/auth/Auth"
import { BASE_URL } from "const/API"
import { getExToken, getToken, logout, saveToken } from "utils/auth/AuthUtils"
import {
  isDataviewPublicOutputsRequest,
  DATAVIEW_PUBLIC_REQUEST_KEY,
} from "utils/DataviewUtils"

const axios = axiosLibrary.create({
  baseURL: BASE_URL,
  timeout: 600000,
})

axios.interceptors.request.use(
  async (config) => {
    config.headers!.Authorization = `Bearer ${getToken()}`
    config.headers!.ExToken = getExToken()

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
    return Promise.reject(error)
  },
)

export default axios
