import { createAsyncThunk } from "@reduxjs/toolkit"

import {
  registerUserApi,
  checkVerificationStatusApi,
  resendVerificationEmailApi,
} from "api/registration/Registration"
import type {
  UserRegistrationRequestDTO,
  UserRegistrationResponseDTO,
  VerificationStatusDTO,
} from "api/registration/RegistrationApiDTO"

/**
 * 型ガード: Axiosエラーかどうかをチェック
 */
interface AxiosError {
  response?: {
    data?: {
      detail?: string | Array<{ msg: string }>
    }
  }
  message?: string
  code?: string
}

const isAxiosError = (error: unknown): error is AxiosError => {
  return (
    typeof error === "object" &&
    error !== null &&
    ("response" in error || "message" in error || "code" in error)
  )
}

/**
 * エラーメッセージを抽出するヘルパー関数
 */
const extractErrorMessage = (
  error: unknown,
  defaultMessage: string,
): string => {
  // Axiosエラーの場合
  if (isAxiosError(error)) {
    // サーバーからのエラーレスポンス
    if (error.response?.data?.detail) {
      const detail = error.response.data.detail

      // Pydanticのバリデーションエラーの場合（配列）
      if (Array.isArray(detail)) {
        return detail.map((err) => err.msg).join(", ")
      }

      // 文字列エラーの場合
      if (typeof detail === "string") {
        return detail
      }
    }

    // ネットワークエラーの場合
    if (error.message === "Network Error") {
      return "ネットワークエラーが発生しました。インターネット接続を確認してください。"
    }

    // タイムアウトエラーの場合
    if (error.code === "ECONNABORTED") {
      return "リクエストがタイムアウトしました。もう一度お試しください。"
    }

    // その他のエラーメッセージがある場合
    if (error.message) {
      return error.message
    }
  }

  // Error オブジェクトの場合
  if (error instanceof Error) {
    return error.message
  }

  // 文字列の場合
  if (typeof error === "string") {
    return error
  }

  // その他の場合はデフォルトメッセージ
  return defaultMessage
}

/**
 * ユーザー登録アクション
 * Firebaseでユーザーを作成し、確認メールを送信
 */
export const registerUser = createAsyncThunk<
  UserRegistrationResponseDTO,
  UserRegistrationRequestDTO,
  { rejectValue: string }
>("registration/registerUser", async (data, { rejectWithValue }) => {
  try {
    const response = await registerUserApi(data)
    return response
  } catch (error: unknown) {
    const errorMessage = extractErrorMessage(
      error,
      "登録に失敗しました。もう一度お試しください。",
    )
    return rejectWithValue(errorMessage)
  }
})

/**
 * メール確認状態チェックアクション
 */
export const checkVerificationStatus = createAsyncThunk<
  VerificationStatusDTO,
  string,
  { rejectValue: string }
>(
  "registration/checkVerificationStatus",
  async (email, { rejectWithValue }) => {
    try {
      const response = await checkVerificationStatusApi(email)
      return response
    } catch (error: unknown) {
      const errorMessage = extractErrorMessage(
        error,
        "確認状態のチェックに失敗しました",
      )
      return rejectWithValue(errorMessage)
    }
  },
)

/**
 * 確認メール再送信アクション
 */
export const resendVerificationEmail = createAsyncThunk<
  { success: boolean; message: string },
  string,
  { rejectValue: string }
>(
  "registration/resendVerificationEmail",
  async (email, { rejectWithValue }) => {
    try {
      const response = await resendVerificationEmailApi(email)
      return response
    } catch (error: unknown) {
      const errorMessage = extractErrorMessage(
        error,
        "メール送信に失敗しました",
      )
      return rejectWithValue(errorMessage)
    }
  },
)
