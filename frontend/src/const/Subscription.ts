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

export const SubscriptionAlertThresholds = {
  WARNING: 90,
  CRITICAL: 100,
} as const

// Storage usage display thresholds (for visual indicators)
export const StorageDisplayThresholds = {
  NEAR_LIMIT_PERCENT: 80, // Show warning color when usage exceeds this
  OVER_LIMIT_PERCENT: 100, // Show error color when usage exceeds this
} as const

// Subscription plan names (matches backend PlanName enum)
export enum PlanName {
  FREE = "Free",
  PREMIUM = "Premium",
}

// User tier identifiers for API/routing (matches backend SubscriptionType enum)
export enum UserTier {
  PREMIUM = "premium",
  FREE = "free",
}

// Subscription user status (matches backend SubscriptionUserStatus enum)
export enum SubscriptionUserStatus {
  FREE = 1,
  SUBSCRIBED = 2,
  EXPIRED = 3,
  CANCELED = 4,
}

// Subscription status labels (matches backend SubscriptionStatus enum)
export enum SubscriptionStatus {
  FREE = "Free",
  PREMIUM = "Premium",
  LIMIT_GRACE = "Limit Grace",
  EXPIRED = "Expired",
}

// Limit alert types (used in storage and subscription warnings)
export enum LimitAlertType {
  STORAGE = "storage",
  GRACE = "grace",
  OVERDUE = "overdue",
}

// Premium instance timing constants (in minutes)
export const PremiumTiming = {
  // Duration of inactivity warning countdown before instance is released
  INACTIVITY_WARNING_DURATION_MINUTES: 60,
  // Interval for updating the countdown display (in milliseconds)
  WARNING_UPDATE_INTERVAL_MS: 60 * 1000, // 1 minute
} as const
