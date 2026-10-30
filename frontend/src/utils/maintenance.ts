export const MAINTENANCE_MARKER = "<!-- optinist-maintenance -->"
export const MAINTENANCE_RELOAD_KEY = "maintenanceReloadAt"
const MIN_RELOAD_INTERVAL_MS = 60_000
const PROBE_TIMEOUT_MS = 5_000

let probe: Promise<boolean> | null = null

const reloadedRecently = (): boolean => {
  try {
    const last = Number(sessionStorage.getItem(MAINTENANCE_RELOAD_KEY))
    return Date.now() - last < MIN_RELOAD_INTERVAL_MS
  } catch {
    return false
  }
}

const probeAndReload = async (): Promise<boolean> => {
  if (reloadedRecently()) return false
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS)
  try {
    // Same-origin, so the ALB's CORS-less maintenance page stays readable.
    const res = await fetch("/", {
      cache: "no-store",
      signal: controller.signal,
    })
    if (res.status !== 503) return false
    // The abort also fails a stalled body read.
    if (!(await res.text()).includes(MAINTENANCE_MARKER)) return false
  } catch {
    return false
  } finally {
    clearTimeout(timer)
  }
  try {
    sessionStorage.setItem(MAINTENANCE_RELOAD_KEY, String(Date.now()))
  } catch {
    // Blocked storage only loses the reload guard.
  }
  window.location.reload()
  return true
}

/**
 * Reloads the tab onto the ALB maintenance page when the site is in
 * maintenance mode. Concurrent callers share one probe.
 */
export const reloadIfUnderMaintenance = (): Promise<boolean> => {
  probe ??= probeAndReload().finally(() => {
    probe = null
  })
  return probe
}
