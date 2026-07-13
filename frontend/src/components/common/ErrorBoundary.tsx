import React, { Component, ErrorInfo, ReactNode } from "react"

import Box from "@mui/material/Box"
import Button from "@mui/material/Button"
import Typography from "@mui/material/Typography"

import { isChunkLoadError, triggerChunkReload } from "utils/chunkLoadReload"

interface ErrorBoundaryProps {
  children: ReactNode
  fallback?: ReactNode
  onError?: (error: Error, errorInfo: ErrorInfo) => void
}

interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
  isReloading: boolean
}

class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = { hasError: false, error: null, isReloading: false }
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    if (isChunkLoadError(error)) {
      return { hasError: false, error: null, isReloading: true }
    }
    return { hasError: true, error, isReloading: false }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    if (isChunkLoadError(error)) {
      // Skip console.error/onError so the patched reporter doesn't ship deploy-time noise.
      if (!triggerChunkReload()) {
        // Guard suppressed the reload — show error UI so the user isn't stranded blank.
        this.setState({ hasError: true, error, isReloading: false })
      }
      return
    }
    // eslint-disable-next-line no-console
    console.error("ErrorBoundary caught an error:", error, errorInfo)
    this.props.onError?.(error, errorInfo)
  }

  handleReload = (): void => {
    window.location.reload()
  }

  handleRetry = (): void => {
    this.setState({ hasError: false, error: null, isReloading: false })
  }

  render(): ReactNode {
    if (this.state.isReloading) {
      return null
    }
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback
      }

      return (
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            minHeight: "200px",
            p: 4,
            textAlign: "center",
          }}
        >
          <Typography variant="h5" gutterBottom>
            Something went wrong
          </Typography>
          <Typography color="text.secondary" paragraph sx={{ maxWidth: 500 }}>
            {this.state.error?.message || "An unexpected error occurred"}
          </Typography>
          <Box sx={{ display: "flex", gap: 2, mt: 2 }}>
            <Button variant="outlined" onClick={this.handleRetry}>
              Try Again
            </Button>
            <Button variant="contained" onClick={this.handleReload}>
              Reload Page
            </Button>
          </Box>
        </Box>
      )
    }

    return this.props.children
  }
}

export default ErrorBoundary
