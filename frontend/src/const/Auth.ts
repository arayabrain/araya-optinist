// Authentication paths
export const AUTH_PATHS = {
  LOGIN: "/login",
  REGISTER: "/register",
  LOGOUT: "/logout",
} as const

export const regexPassword =
  /^(?=.*\d)(?=.*[!#$%&()*+,-./@_|])(?=.*[a-zA-Z]).{6,255}$/

export const regexEmail =
  /^(([^<>()[\]\\.,;:\s@"]+(\.[^<>()[\]\\.,;:\s@"]+)*)|(".+"))@((\[[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\])|(([a-zA-Z\-0-9]+\.)+[a-zA-Z]{2,}))$/

export const regexIgnoreS = /[^!#$%&()*+,-./@_|a-zA-Z0-9]/
