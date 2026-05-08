// Re-arm (half-open circuit) configuration for instance-unreachable recovery.
export const INITIAL_PROBE_DELAY_MS = 30000
export const MAX_PROBE_DELAY_MS = 300000
export const PROBE_BACKOFF_MULTIPLIER = 2
export const MAX_FAILED_PROBES = 5

// Suppress the first instance_unreachable signal for this long after a
// shared → dedicated transition (or a dedicated reassignment to a different
// instance). The dedicated ALB target group can return a single transient
// 5xx during warm-up, which is not a true outage and should not overwrite
// the success toast with a warning popup.
export const DEDICATED_HANDOFF_GRACE_MS = 15000

// localStorage fallback because BroadcastChannel doesn't replay past messages to new tabs.
export const LS_UNREACHABLE_SNAPSHOT = "premium_unreachable_snapshot"
export const UNREACHABLE_SNAPSHOT_TTL_MS = 60 * 60 * 1000
