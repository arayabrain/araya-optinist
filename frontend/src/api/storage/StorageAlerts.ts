import axios from "utils/axios"

export interface StorageAlert {
  user_id: number
  user_name: string
  user_email: string
  alert_level: "critical" | "danger"
  storage_usage_bytes: number
  storage_quota_bytes: number
  storage_usage_percent: number
  timestamp: string
  message: string
  subscription_plan: string
}

export interface StorageUsage {
  storage_usage_bytes: number
  storage_usage_formatted: string
  storage_quota_bytes: number | null
  storage_quota_formatted: string | null
  storage_usage_percent: number | null
  alert_level: "critical" | "danger" | null
  thresholds: {
    critical: number
    danger: number
  }
}

export interface StorageAlertResponse {
  has_alert: boolean
  storage_usage_bytes?: number
  storage_usage_formatted?: string
  alert: StorageAlert | null
}

export interface RefreshStorageResponse {
  success: boolean
  updated_usage_bytes: number
  updated_usage_formatted: string
  database_updated: boolean
}

export const getMyStorageAlertApi = async (): Promise<StorageAlertResponse> => {
  const response = await axios.get("/storage-alerts/me")
  return response.data
}

export const getMyStorageUsageApi = async (): Promise<StorageUsage> => {
  const response = await axios.get("/storage-alerts/usage")
  return response.data
}

export const getAllStorageAlertsApi = async (): Promise<StorageAlert[]> => {
  const response = await axios.get("/storage-alerts/all")
  return response.data
}

export const refreshStorageUsageApi =
  async (): Promise<RefreshStorageResponse> => {
    const response = await axios.post("/storage-alerts/refresh")
    return response.data
  }

// Limit Warning Types
export interface LimitWarning {
  has_warning: boolean
  warning_type: "storage" | "grace" | "overdue"
  days_remaining: number
  excess_data_bytes: number
  excess_data_gb: number
  storage_usage_bytes: number
  storage_usage_gb: number
  storage_quota_bytes: number
  storage_quota_gb: number
  subscription_end_date?: string
  grace_end_date?: string
  deletion_date: string
  message: string
}

export interface LimitWarningStatus {
  has_warning: boolean
  warning_type: string | null
  days_remaining: number | null
  user_id: number
}

// Limit Warning API Functions
export const getMyLimitWarningApi = async (): Promise<LimitWarning | null> => {
  const response = await axios.get("/storage-alerts/limit-warning")
  return response.data
}

export const checkLimitWarningStatusApi =
  async (): Promise<LimitWarningStatus> => {
    const response = await axios.get("/storage-alerts/limit-warning/check")
    return response.data
  }
