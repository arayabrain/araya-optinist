/**
 * Subscription-related constants
 * These values should match the backend SubscriptionPeriods class in
 * studio/app/common/models/subscription.py
 */

export const SubscriptionPeriods = {
  TRIAL_PERIOD_DAYS: 30,
  GRACE_PERIOD_DAYS: 30,
  WARNING_PERIOD_DAYS: 30,
  STORAGE_WARNING_DAYS: 30,

  // Progress calculation constants
  MAX_PROGRESS_PERCENT: 100,
  MIN_PROGRESS_PERCENT: 0,
  PROGRESS_REFERENCE_DAYS: 30,

  // Warning color thresholds (days remaining)
  CRITICAL_THRESHOLD_DAYS: 0,
  URGENT_THRESHOLD_DAYS: 7,
  WARNING_THRESHOLD_DAYS: 14,
} as const

export const SubscriptionPlanIds = {
  FREE: 1,
  PREMIUM: 2,
} as const

export const SubscriptionAlertThresholds = {
  WARNING: 90,
  CRITICAL: 100,
} as const

// Storage usage display thresholds (for visual indicators)
export const StorageDisplayThresholds = {
  NEAR_LIMIT_PERCENT: 80, // Show warning color when usage exceeds this
  OVER_LIMIT_PERCENT: 100, // Show error color when usage exceeds this
} as const
