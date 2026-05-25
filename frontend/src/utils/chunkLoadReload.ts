const RELOAD_FLAG_KEY = "chunk-reload-attempted"
const CHUNK_ERROR_PATTERN =
  /Loading (?:CSS )?chunk [^\s]+ failed|ChunkLoadError/
const GUARD_CLEAR_DELAY_MS = 30_000

// Captured before errorReporter patches console.warn — keeps reload noise off the log queue.
const _originalWarn = console.warn.bind(console)

let _initialized = false
let _guardClearTimer: ReturnType<typeof setTimeout> | null = null

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

  window.addEventListener("error", (event) => {
    if (isChunkLoadError(event.error) || isChunkLoadError(event.message)) {
      triggerChunkReload()
    }
  })

  window.addEventListener("unhandledrejection", (event) => {
    if (isChunkLoadError(event.reason)) {
      triggerChunkReload()
    }
  })

  // Delay the clear so a chunk failure firing just after `load` can't bypass the guard.
  window.addEventListener("load", () => {
    _guardClearTimer = setTimeout(() => {
      try {
        sessionStorage.removeItem(RELOAD_FLAG_KEY)
      } catch {
        // No-op.
      }
    }, GUARD_CLEAR_DELAY_MS)
  })
}

export function _resetForTesting() {
  _initialized = false
  if (_guardClearTimer) {
    clearTimeout(_guardClearTimer)
    _guardClearTimer = null
  }
  try {
    sessionStorage.removeItem(RELOAD_FLAG_KEY)
  } catch {
    // No-op.
  }
}
