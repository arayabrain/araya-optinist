import axios from "utils/axios"

export interface StorageAlert {
  user_id: number
  user_name: string
  user_email: string
  alert_level: "critical" | "danger"
  usage_bytes: number
  quota_bytes: number
  usage_percentage: number
  timestamp: string
  message: string
  subscription_tier: string
}

export interface StorageUsage {
  usage_bytes: number
  usage_formatted: string
  quota_bytes: number | null
  quota_formatted: string | null
  usage_percentage: number | null
  alert_level: "critical" | "danger" | null
  thresholds: {
    critical: number
    danger: number
  }
}

export interface StorageAlertResponse {
  has_alert: boolean
  current_usage_bytes?: number
  current_usage_formatted?: string
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

// Downgrade Warning Types
export interface DowngradeWarning {
  has_warning: boolean
  warning_type: "immediate" | "downgrade" | "overdue"
  days_remaining: number
  excess_data_bytes: number
  excess_data_gb: number
  current_usage_bytes: number
  current_usage_gb: number
  free_tier_limit_bytes: number
  free_tier_limit_gb: number
  subscription_end_date?: string
  grace_end_date?: string
  deletion_date: string
  message: string
}

export interface DowngradeWarningStatus {
  has_warning: boolean
  warning_type: string | null
  days_remaining: number | null
  user_id: number
}

// Downgrade Warning API Functions
export const getMyDowngradeWarningApi =
  async (): Promise<DowngradeWarning | null> => {
    const response = await axios.get("/storage-alerts/downgrade-warning")
    return response.data
  }

export const checkDowngradeWarningStatusApi =
  async (): Promise<DowngradeWarningStatus> => {
    const response = await axios.get("/storage-alerts/downgrade-warning/check")
    return response.data
  }
