import { BASE_URL } from "const/API"
import { getToken } from "utils/auth/AuthUtils"

export const MAX_QUEUE_SIZE = 20
export const MAX_MESSAGE_LENGTH = 2000
const FLUSH_INTERVAL_MS = 5000
const ENDPOINT = `${BASE_URL}/users/me/frontend-errors`

const FRONTEND_LOG_LEVELS = ["error", "warn"] as const
type FrontendLogLevel = (typeof FRONTEND_LOG_LEVELS)[number]

interface ErrorEntry {
  level: FrontendLogLevel
  message: string
  source?: string
  url?: string
  timestamp: string
}

let _queue: ErrorEntry[] = []
let _initialized = false
let _suppressed = false
let _inOnerror = false
let _flushTimer: ReturnType<typeof setInterval> | null = null
let _originalError: typeof console.error
let _originalWarn: typeof console.warn

function serializeArgs(args: unknown[]): string {
  const seen = new WeakSet()

  const parts = args.map((arg) => {
    if (arg instanceof Error) {
      return arg.stack || arg.message
    }
    if (typeof arg === "object" && arg !== null) {
      try {
        return JSON.stringify(arg, (_key, value) => {
          if (typeof value === "object" && value !== null) {
            if (seen.has(value)) return "[circular]"
            seen.add(value)
          }
          return value
        })
      } catch {
        return "[unserializable]"
      }
    }
    try {
      return String(arg)
    } catch {
      return "[unserializable]"
    }
  })

  const message = parts.join(" ")
  if (message.length > MAX_MESSAGE_LENGTH) {
    return message.slice(0, MAX_MESSAGE_LENGTH)
  }
  return message
}

function enqueue(level: FrontendLogLevel, args: unknown[], source?: string) {
  if (_suppressed) return

  const entry: ErrorEntry = {
    level,
    message: serializeArgs(args),
    url: window.location.href,
    timestamp: new Date().toISOString(),
  }

  if (source) {
    entry.source = source
  }

  _queue.push(entry)
  if (_queue.length > MAX_QUEUE_SIZE) {
    _queue = _queue.slice(-MAX_QUEUE_SIZE)
  }
}

export function flushErrors() {
  if (_queue.length === 0) return

  const token = getToken()
  if (!token) return

  const batch = _queue
  _queue = []

  // Use raw fetch instead of the axios singleton because:
  // 1. `keepalive: true` is required for reliable delivery during beforeunload
  //    (axios does not support keepalive)
  // 2. The axios response interceptor calls console.error on 401s, which would
  //    create an infinite loop through this reporter
  _suppressed = true
  fetch(ENDPOINT, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ errors: batch }),
    keepalive: true,
  })
    .catch(() => {
      // Silently swallow network errors
    })
    .finally(() => {
      _suppressed = false
    })
}

export function initErrorReporter() {
  if (_initialized) return
  _initialized = true

  _originalError = console.error
  _originalWarn = console.warn

  console.error = (...args: unknown[]) => {
    _originalError.apply(console, args)
    if (!_inOnerror) {
      enqueue("error", args)
    }
  }

  console.warn = (...args: unknown[]) => {
    _originalWarn.apply(console, args)
    enqueue("warn", args)
  }

  const prevOnError = window.onerror
  window.onerror = (message, source, lineno, colno, error) => {
    _inOnerror = true
    try {
      enqueue(
        "error",
        [error || message],
        typeof source === "string" ? source : undefined,
      )
      if (prevOnError) {
        return prevOnError(message, source, lineno, colno, error)
      }
    } finally {
      _inOnerror = false
    }
  }

  window.addEventListener("unhandledrejection", (event) => {
    enqueue("error", [event.reason])
  })

  _flushTimer = setInterval(flushErrors, FLUSH_INTERVAL_MS)

  window.addEventListener("beforeunload", () => {
    flushErrors()
  })
}

// Exported for testing
export function _resetForTesting() {
  if (_flushTimer) clearInterval(_flushTimer)
  _flushTimer = null
  _queue = []
  _initialized = false
  _suppressed = false
  _inOnerror = false
  if (_originalError) console.error = _originalError
  if (_originalWarn) console.warn = _originalWarn
}

export function _getQueue() {
  return _queue
}
