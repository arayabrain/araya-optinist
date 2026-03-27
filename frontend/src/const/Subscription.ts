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

// Subscription plan names (extensible for multiple tiers)
// Note: These are display names. Actual tier identifiers come from the backend
export enum PlanName {
  FREE = "Free",
  PREMIUM = "Premium",
  UNKNOWN = "Unknown",
}

// User tier identifiers for API/routing (matches backend SubscriptionType enum)
// Extensible to support any tier
export enum UserTier {
  FREE = "free",
  PREMIUM = "premium",
}

// Helper to get PlanName enum from tier string
export const getPlanNameFromTier = (tier?: string): string => {
  if (!tier) return PlanName.UNKNOWN

  const tierLower = tier.toLowerCase()
  switch (tierLower) {
    case "free":
      return PlanName.FREE
    case "premium":
      return PlanName.PREMIUM
    default:
      // Capitalize first letter for unknown tiers
      return tier.charAt(0).toUpperCase() + tier.slice(1)
  }
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

// Premium instance timing constants
export const PremiumTiming = {
  // Backend activity cache TTL (must match studio middleware _CACHE_TTL_SECONDS)
  ACTIVITY_CACHE_TTL_SECONDS: 60, // 1 minute
  // Duration of inactivity warning countdown before instance is released
  INACTIVITY_WARNING_DURATION_MINUTES: 60,
  // Buffer to account for cache staleness when calculating timeouts
  INACTIVITY_BUFFER_MINUTES: 2,
  // Interval for updating the countdown display (in milliseconds)
  WARNING_UPDATE_INTERVAL_MS: 60 * 1000, // 1 minute
} as const

// Deletion priority options (matches backend DeletionPriority enum)
export enum DeletionPriority {
  PRESERVE_OUTPUTS = "preserve_outputs",
  PRESERVE_INPUTS = "preserve_inputs",
}

// User-facing messages for data deleted by expiration lifecycle
export const ExpirationMessages = {
  ALL_DATA_DELETED:
    "Data deleted due to subscription expiration. Config files preserved.",
  INTERMEDIATES_AND_OUTPUTS:
    "Visualization and output data deleted. Reproduce still available.",
  INTERMEDIATES_AND_INPUTS:
    "Visualization and input data deleted due to subscription expiration. NWB download still available. Reproduce available after re-uploading inputs.",
  INTERMEDIATES_ONLY:
    "Visualization data deleted due to subscription expiration. NWB download and reproduce still available.",
  INPUTS_ONLY:
    "Input data deleted due to subscription expiration. Reproduce available after re-uploading inputs.",
  GENERIC: "Some data deleted due to subscription expiration.",
  NWB_DELETED: "NWB data deleted due to subscription expiration",
  OUTPUTS_DELETED: "Output data deleted due to subscription expiration",
  VISUALIZATION_DELETED:
    "Visualization data deleted due to subscription expiration.",
  DATA_UNSYNCHRONIZED: "Data is unsynchronized",
} as const

// HTTP header names for ALB routing
// These headers are used to route premium users to their dedicated instances
export const RoutingHeaders = {
  // Secure, non-reversible routing token (HMAC-SHA256)
  ROUTING_ID: "X-Routing-ID",
  // User subscription tier indicator
  USER_TIER: "X-User-Tier",
} as const
