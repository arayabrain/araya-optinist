import { render, screen, fireEvent } from "@testing-library/react"

import ErrorBoundary from "components/common/ErrorBoundary"

const ThrowError = ({ shouldThrow }: { shouldThrow: boolean }) => {
  if (shouldThrow) {
    throw new Error("Test error message")
  }
  return <div>No error</div>
}

// Controllable component that uses external state for throw behavior
let shouldThrowExternal = false
const ControllableThrowError = () => {
  if (shouldThrowExternal) {
    throw new Error("Test error message")
  }
  return <div>No error</div>
}

describe("ErrorBoundary", () => {
  const originalConsoleError = console.error
  let originalLocation: Location

  beforeEach(() => {
    console.error = jest.fn()
    originalLocation = window.location
  })

  afterEach(() => {
    console.error = originalConsoleError
    shouldThrowExternal = false
    Object.defineProperty(window, "location", {
      value: originalLocation,
      writable: true,
      configurable: true,
    })
  })

  it("should render children when no error occurs", () => {
    render(
      <ErrorBoundary>
        <div>Child content</div>
      </ErrorBoundary>,
    )

    expect(screen.getByText("Child content")).toBeInTheDocument()
  })

  it("should render error UI when child throws", () => {
    render(
      <ErrorBoundary>
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>,
    )

    expect(screen.getByText("Something went wrong")).toBeInTheDocument()
    expect(screen.getByText("Test error message")).toBeInTheDocument()
  })

  it("should render custom fallback when provided", () => {
    render(
      <ErrorBoundary fallback={<div>Custom fallback</div>}>
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>,
    )

    expect(screen.getByText("Custom fallback")).toBeInTheDocument()
    expect(screen.queryByText("Something went wrong")).not.toBeInTheDocument()
  })

  it("should call onError callback when error occurs", () => {
    const onError = jest.fn()

    render(
      <ErrorBoundary onError={onError}>
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>,
    )

    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError).toHaveBeenCalledWith(
      expect.any(Error),
      expect.objectContaining({ componentStack: expect.any(String) }),
    )
  })

  it("should render reload and try again buttons", () => {
    render(
      <ErrorBoundary>
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>,
    )

    expect(screen.getByText("Try Again")).toBeInTheDocument()
    expect(screen.getByText("Reload Page")).toBeInTheDocument()
  })

  it("should recover when Try Again is clicked and error is fixed", () => {
    // Use external state so the component doesn't throw when re-rendered
    shouldThrowExternal = true

    render(
      <ErrorBoundary>
        <ControllableThrowError />
      </ErrorBoundary>,
    )

    expect(screen.getByText("Something went wrong")).toBeInTheDocument()

    // Fix the error condition BEFORE clicking Try Again
    shouldThrowExternal = false

    fireEvent.click(screen.getByText("Try Again"))

    expect(screen.getByText("No error")).toBeInTheDocument()
    expect(screen.queryByText("Something went wrong")).not.toBeInTheDocument()
  })

  it("should reload page when Reload Page is clicked", () => {
    const reloadMock = jest.fn()
    Object.defineProperty(window, "location", {
      value: { reload: reloadMock },
      writable: true,
      configurable: true,
    })

    render(
      <ErrorBoundary>
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>,
    )

    fireEvent.click(screen.getByText("Reload Page"))
    expect(reloadMock).toHaveBeenCalled()
  })

  it("should display generic message when error has no message", () => {
    const ThrowEmptyError = () => {
      throw new Error()
    }

    render(
      <ErrorBoundary>
        <ThrowEmptyError />
      </ErrorBoundary>,
    )

    expect(screen.getByText("An unexpected error occurred")).toBeInTheDocument()
  })

  it("should reload the page when a ChunkLoadError is thrown", () => {
    const reloadMock = jest.fn()
    Object.defineProperty(window, "location", {
      value: { reload: reloadMock },
      writable: true,
      configurable: true,
    })
    sessionStorage.removeItem("chunk-reload-attempted")

    const ThrowChunkError = () => {
      const error = new Error("Loading chunk 17 failed.")
      error.name = "ChunkLoadError"
      throw error
    }

    render(
      <ErrorBoundary>
        <ThrowChunkError />
      </ErrorBoundary>,
    )

    expect(reloadMock).toHaveBeenCalledTimes(1)
    sessionStorage.removeItem("chunk-reload-attempted")
  })

  it("should not log or call onError for a ChunkLoadError", () => {
    const reloadMock = jest.fn()
    Object.defineProperty(window, "location", {
      value: { reload: reloadMock },
      writable: true,
      configurable: true,
    })
    sessionStorage.removeItem("chunk-reload-attempted")
    const onError = jest.fn()

    const ThrowChunkError = () => {
      const error = new Error("Loading chunk 17 failed.")
      error.name = "ChunkLoadError"
      throw error
    }

    render(
      <ErrorBoundary onError={onError}>
        <ThrowChunkError />
      </ErrorBoundary>,
    )

    expect(onError).not.toHaveBeenCalled()
    expect(console.error).not.toHaveBeenCalledWith(
      "ErrorBoundary caught an error:",
      expect.anything(),
      expect.anything(),
    )
    sessionStorage.removeItem("chunk-reload-attempted")
  })

  it("should show the error UI when the reload guard suppresses the reload", () => {
    const reloadMock = jest.fn()
    Object.defineProperty(window, "location", {
      value: { reload: reloadMock },
      writable: true,
      configurable: true,
    })
    sessionStorage.setItem("chunk-reload-attempted", "1")

    const ThrowChunkError = () => {
      const error = new Error("Loading chunk 17 failed.")
      error.name = "ChunkLoadError"
      throw error
    }

    render(
      <ErrorBoundary>
        <ThrowChunkError />
      </ErrorBoundary>,
    )

    expect(reloadMock).not.toHaveBeenCalled()
    expect(screen.getByText("Something went wrong")).toBeInTheDocument()
    sessionStorage.removeItem("chunk-reload-attempted")
  })
})
