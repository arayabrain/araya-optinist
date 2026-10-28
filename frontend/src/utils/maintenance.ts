export const MAINTENANCE_MARKER = "<!-- optinist-maintenance -->"
export const MAINTENANCE_RELOAD_KEY = "maintenanceReloadAt"
const MIN_RELOAD_INTERVAL_MS = 60_000

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
  let res: Response
  try {
    // Same-origin, so the ALB's CORS-less maintenance page stays readable.
    res = await fetch("/", { cache: "no-store" })
  } catch {
    return false
  }
  if (res.status !== 503) return false
  const body = await res.text().catch(() => "")
  if (!body.includes(MAINTENANCE_MARKER)) return false
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
