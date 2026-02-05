import { render, screen, fireEvent } from "@testing-library/react"

import ErrorBoundary from "components/common/ErrorBoundary"

const ThrowError = ({ shouldThrow }: { shouldThrow: boolean }) => {
  if (shouldThrow) {
    throw new Error("Test error message")
  }
  return <div>No error</div>
}

describe("ErrorBoundary", () => {
  const originalConsoleError = console.error

  beforeEach(() => {
    console.error = jest.fn()
  })

  afterEach(() => {
    console.error = originalConsoleError
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
    const { rerender } = render(
      <ErrorBoundary>
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>,
    )

    expect(screen.getByText("Something went wrong")).toBeInTheDocument()

    fireEvent.click(screen.getByText("Try Again"))

    rerender(
      <ErrorBoundary>
        <ThrowError shouldThrow={false} />
      </ErrorBoundary>,
    )

    expect(screen.getByText("No error")).toBeInTheDocument()
    expect(screen.queryByText("Something went wrong")).not.toBeInTheDocument()
  })

  it("should reload page when Reload Page is clicked", () => {
    const reloadMock = jest.fn()
    Object.defineProperty(window, "location", {
      value: { reload: reloadMock },
      writable: true,
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
})
