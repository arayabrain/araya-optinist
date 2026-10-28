import axiosLibrary from "axios"

import { API_TIMEOUT, BASE_URL } from "const/API"
import { getRefreshToken, removeRefreshToken } from "utils/auth/AuthUtils"
import axios from "utils/axios"

export type LoginDTO = {
  email: string
  password: string
}

export type TokenDTO = {
  access_token: string
  token_type: string
  refresh_token: string
  ex_token: string
}
export type AccessTokenDTO = {
  access_token: string
}

// Asks the backend to keep the refresh token in an httpOnly cookie.
const REFRESH_COOKIE = {
  withCredentials: true,
  headers: { "X-Refresh-Cookie": "1" },
}

export const loginApi = async (data: LoginDTO): Promise<TokenDTO> => {
  const response = await axios.post("/auth/login", data, REFRESH_COOKIE)
  return response.data
}

let refreshing: Promise<AccessTokenDTO> | null = null

// Bypasses the shared instance: its 401 handler logs out, which is wrong for a
// visitor with no session, and its premium routing headers are not needed here.
export const refreshTokenApi = (): Promise<AccessTokenDTO> => {
  const post = (body: object) =>
    axiosLibrary.post<AccessTokenDTO>(`${BASE_URL}/auth/refresh`, body, {
      ...REFRESH_COOKIE,
      timeout: API_TIMEOUT.REFRESH,
    })
  refreshing ??= (async () => {
    const legacy = getRefreshToken()
    if (legacy) {
      try {
        // Used until it expires; the backend never moves it into the cookie.
        return (await post({ refresh_token: legacy })).data
      } catch (e) {
        const status = axiosLibrary.isAxiosError(e) ? e.response?.status : 0
        if (!status || status >= 500) throw e
        // A dead token from an older SPA must not shadow a live cookie.
        removeRefreshToken()
      }
    }
    return (await post({})).data
  })().finally(() => {
    refreshing = null
  })
  return refreshing
}

export const logoutApi = async (): Promise<void> => {
  await axiosLibrary.post(`${BASE_URL}/auth/logout`, undefined, {
    withCredentials: true,
    timeout: API_TIMEOUT.LOGOUT,
  })
}

export const sendResetPasswordMailApi = async (
  email: string,
): Promise<string> => {
  const response = await axios.post(
    `/auth/send_reset_password_mail?email=${email}`,
  )
  return response.data
}

export const proxyLoginApi = async (uid: string): Promise<TokenDTO> => {
  const response = await axios.post(
    `/auth/proxy-login/${uid}`,
    undefined,
    REFRESH_COOKIE,
  )
  return response.data
}
