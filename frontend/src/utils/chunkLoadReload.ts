const RELOAD_FLAG_KEY = "chunk-reload-attempted"
const CHUNK_ERROR_PATTERN =
  /Loading (?:CSS )?chunk [^\s]+ failed|ChunkLoadError/
const GUARD_CLEAR_DELAY_MS = 30_000

// Captured before errorReporter patches console.warn — keeps reload noise off the log queue.
const _originalWarn = console.warn.bind(console)

let _initialized = false
let _guardClearTimer: ReturnType<typeof setTimeout> | null = null
let _errorListener: ((event: ErrorEvent) => void) | null = null
let _rejectionListener: ((event: PromiseRejectionEvent) => void) | null = null
let _loadListener: (() => void) | null = null

export function isChunkLoadError(value: unknown): boolean {
  if (!value) return false

  if (typeof value === "string") {
    return CHUNK_ERROR_PATTERN.test(value)
  }

  if (typeof value === "object") {
    const candidate = value as { name?: unknown; message?: unknown }
    if (candidate.name === "ChunkLoadError") return true
    if (typeof candidate.message === "string") {
      return CHUNK_ERROR_PATTERN.test(candidate.message)
    }
  }

  return false
}

export function triggerChunkReload(): boolean {
  _originalWarn(
    "[chunkLoadReload] Chunk load error detected; attempting reload",
  )

  let alreadyAttempted = false
  try {
    alreadyAttempted = sessionStorage.getItem(RELOAD_FLAG_KEY) === "1"
  } catch {
    // sessionStorage may be unavailable (e.g. privacy mode); fall through.
  }

  if (alreadyAttempted) return false

  try {
    sessionStorage.setItem(RELOAD_FLAG_KEY, "1")
  } catch {
    // Best effort — proceed with reload even if we can't persist the flag.
  }

  window.location.reload()
  return true
}

export function initChunkReloadHandler() {
  if (_initialized) return
  _initialized = true

  _errorListener = (event) => {
    if (isChunkLoadError(event.error) || isChunkLoadError(event.message)) {
      triggerChunkReload()
    }
  }
  window.addEventListener("error", _errorListener)

  _rejectionListener = (event) => {
    if (isChunkLoadError(event.reason)) {
      // Block errorReporter's later listener from shipping deploy-time noise.
      event.stopImmediatePropagation()
      triggerChunkReload()
    }
  }
  window.addEventListener("unhandledrejection", _rejectionListener)

  // Delay the clear so a chunk failure firing just after `load` can't bypass the guard.
  _loadListener = () => {
    _guardClearTimer = setTimeout(() => {
      try {
        sessionStorage.removeItem(RELOAD_FLAG_KEY)
      } catch {
        // No-op.
      }
    }, GUARD_CLEAR_DELAY_MS)
  }
  window.addEventListener("load", _loadListener)
}

export function _resetForTesting() {
  _initialized = false
  if (_guardClearTimer) {
    clearTimeout(_guardClearTimer)
    _guardClearTimer = null
  }
  if (_errorListener) {
    window.removeEventListener("error", _errorListener)
    _errorListener = null
  }
  if (_rejectionListener) {
    window.removeEventListener("unhandledrejection", _rejectionListener)
    _rejectionListener = null
  }
  if (_loadListener) {
    window.removeEventListener("load", _loadListener)
    _loadListener = null
  }
  try {
    sessionStorage.removeItem(RELOAD_FLAG_KEY)
  } catch {
    // No-op.
  }
}
