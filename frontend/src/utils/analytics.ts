import { safeLocalStorage } from "utils/safeStorage"

export const CONSENT_STORAGE_KEY = "analyticsConsent"

export type ConsentDecision = "granted" | "denied"

type AnalyticsEvent = {
  event: string
  params?: Record<string, unknown>
}

const MAX_PENDING_EVENTS = 10

// Must stay identical to the copies in public/index.html, config-overrides.js and ecr_build_push.sh: divergence means a consent notice with nothing behind it.
const GTM_ID_PATTERN = /^GTM-[A-Z0-9]+$/

// Events raised before the visitor answered the banner, so accepting still
// records the entry pageview instead of losing it.
let _pending: AnalyticsEvent[] = []

// Survives a localStorage that refuses writes (Safari "block all cookies",
// private modes), which would otherwise leave GTM granted but nothing pushed.
let _sessionConsent: ConsentDecision | null = null

// Module state is not reactive: the banner and the Account page render side by
// side on a first visit, and the one that did not handle the decision has to
// hear about it.
const _consentListeners = new Set<(decision: ConsentDecision) => void>()

export function isGtmEnabled(): boolean {
  return GTM_ID_PATTERN.test(process.env.REACT_APP_GTM_ID ?? "")
}

export function getAnalyticsConsent(): ConsentDecision | null {
  if (_sessionConsent) return _sessionConsent
  const stored = safeLocalStorage.getItem(CONSENT_STORAGE_KEY)
  return stored === "granted" || stored === "denied" ? stored : null
}

function updateGtagConsent(decision: ConsentDecision): void {
  window.gtag?.("consent", "update", { analytics_storage: decision })
}

export function trackEvent(
  event: string,
  params?: Record<string, unknown>,
): void {
  if (!isGtmEnabled()) return

  const consent = getAnalyticsConsent()
  if (consent === null) {
    if (_pending.length < MAX_PENDING_EVENTS) _pending.push({ event, params })
    return
  }
  if (consent === "denied") return

  window.dataLayer?.push({ ...params, event })
}

export function subscribeAnalyticsConsent(
  listener: (decision: ConsentDecision) => void,
): () => void {
  _consentListeners.add(listener)
  return () => {
    _consentListeners.delete(listener)
  }
}

export function setAnalyticsConsent(decision: ConsentDecision): void {
  _sessionConsent = decision
  safeLocalStorage.setItem(CONSENT_STORAGE_KEY, decision)
  updateGtagConsent(decision)

  // Flush before notifying: a throwing listener would otherwise strand the
  // queued events, neither sent nor cleared.
  const pending = _pending
  _pending = []
  if (decision === "granted") {
    pending.forEach(({ event, params }) => trackEvent(event, params))
  }

  // Isolated: this is exported, so a listener is not necessarily a setState
  // that cannot throw.
  _consentListeners.forEach((listener) => {
    try {
      listener(decision)
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error("Analytics consent listener failed", e)
    }
  })
}

export function initAnalyticsConsent(): void {
  if (!isGtmEnabled()) return
  const consent = getAnalyticsConsent()
  if (consent) updateGtagConsent(consent)
}

export function normalizePath(pathname: string): string {
  return pathname.replace(/\/\d+(?=\/|$)/g, "/:id")
}

export function _resetForTesting(): void {
  _pending = []
  _sessionConsent = null
  _consentListeners.clear()
}
