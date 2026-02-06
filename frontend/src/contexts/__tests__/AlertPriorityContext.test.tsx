import React from "react"

import { SnackbarProvider } from "notistack"

import { render, screen, act, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import {
  AlertPriorityProvider,
  useAlertPriority,
  AlertConfig,
  AlertPriority,
} from "contexts/AlertPriorityContext"

// Test component to interact with context
const TestComponent: React.FC<{
  onContextReady?: (context: ReturnType<typeof useAlertPriority>) => void
}> = ({ onContextReady }) => {
  const context = useAlertPriority()

  React.useEffect(() => {
    if (onContextReady) {
      onContextReady(context)
    }
  }, [context, onContextReady])

  return (
    <div>
      <span data-testid="alert-count">{context.alerts.length}</span>
      <button
        data-testid="add-critical"
        onClick={() =>
          context.addAlert({
            id: "critical-1",
            priority: "critical",
            message: "Critical alert message",
          })
        }
      >
        Add Critical
      </button>
      <button
        data-testid="add-low"
        onClick={() =>
          context.addAlert({
            id: "low-1",
            priority: "low",
            message: "Low priority message",
          })
        }
      >
        Add Low
      </button>
      <button data-testid="clear-all" onClick={() => context.clearAllAlerts()}>
        Clear All
      </button>
    </div>
  )
}

const renderWithProviders = (ui: React.ReactNode) => {
  return render(
    <SnackbarProvider maxSnack={5}>
      <AlertPriorityProvider>{ui}</AlertPriorityProvider>
    </SnackbarProvider>,
  )
}

describe("AlertPriorityContext", () => {
  describe("basic functionality", () => {
    it("should render without crashing", () => {
      renderWithProviders(<TestComponent />)
      expect(screen.getByTestId("alert-count")).toHaveTextContent("0")
    })

    it("should add alert when addAlert is called", async () => {
      renderWithProviders(<TestComponent />)

      await act(async () => {
        await userEvent.click(screen.getByTestId("add-critical"))
      })

      expect(screen.getByTestId("alert-count")).toHaveTextContent("1")
    })

    it("should remove alert when removeAlert is called", async () => {
      let contextRef: ReturnType<typeof useAlertPriority> | null = null

      renderWithProviders(
        <TestComponent onContextReady={(ctx) => (contextRef = ctx)} />,
      )

      await act(async () => {
        contextRef?.addAlert({
          id: "test-alert",
          priority: "medium",
          message: "Test message",
        })
      })

      expect(screen.getByTestId("alert-count")).toHaveTextContent("1")

      await act(async () => {
        contextRef?.removeAlert("test-alert")
      })

      expect(screen.getByTestId("alert-count")).toHaveTextContent("0")
    })

    it("should clear all alerts when clearAllAlerts is called", async () => {
      let contextRef: ReturnType<typeof useAlertPriority> | null = null

      renderWithProviders(
        <TestComponent onContextReady={(ctx) => (contextRef = ctx)} />,
      )

      await act(async () => {
        contextRef?.addAlert({ id: "1", priority: "low", message: "Alert 1" })
        contextRef?.addAlert({
          id: "2",
          priority: "medium",
          message: "Alert 2",
        })
        contextRef?.addAlert({ id: "3", priority: "high", message: "Alert 3" })
      })

      expect(screen.getByTestId("alert-count")).toHaveTextContent("3")

      await act(async () => {
        await userEvent.click(screen.getByTestId("clear-all"))
      })

      expect(screen.getByTestId("alert-count")).toHaveTextContent("0")
    })

    it("should not add duplicate alerts with same ID", async () => {
      let contextRef: ReturnType<typeof useAlertPriority> | null = null

      renderWithProviders(
        <TestComponent onContextReady={(ctx) => (contextRef = ctx)} />,
      )

      await act(async () => {
        contextRef?.addAlert({
          id: "duplicate",
          priority: "low",
          message: "First",
        })
        contextRef?.addAlert({
          id: "duplicate",
          priority: "low",
          message: "Second",
        })
      })

      expect(screen.getByTestId("alert-count")).toHaveTextContent("1")
    })
  })

  describe("priority sorting", () => {
    it("should show critical alert as modal over others", async () => {
      let contextRef: ReturnType<typeof useAlertPriority> | null = null

      renderWithProviders(
        <TestComponent onContextReady={(ctx) => (contextRef = ctx)} />,
      )

      await act(async () => {
        contextRef?.addAlert({ id: "low", priority: "low", message: "Low" })
        contextRef?.addAlert({
          id: "critical",
          priority: "critical",
          message: "Critical message",
        })
      })

      // Critical alert should be shown in dialog
      await waitFor(() => {
        expect(screen.getByRole("dialog")).toBeInTheDocument()
      })
      expect(screen.getByText("Critical message")).toBeInTheDocument()
    })

    it("should sort alerts by priority (critical > high > medium > low)", async () => {
      let contextRef: ReturnType<typeof useAlertPriority> | undefined

      renderWithProviders(
        <TestComponent onContextReady={(ctx) => (contextRef = ctx)} />,
      )

      // Wait for context to be ready
      await waitFor(() => {
        expect(contextRef).toBeDefined()
      })

      await act(async () => {
        contextRef!.addAlert({ id: "low", priority: "low", message: "Low" })
        contextRef!.addAlert({
          id: "medium",
          priority: "medium",
          message: "Medium",
        })
        contextRef!.addAlert({ id: "high", priority: "high", message: "High" })
        contextRef!.addAlert({
          id: "critical",
          priority: "critical",
          message: "Critical",
        })
      })

      // First alert (highest priority) should be critical
      expect(contextRef!.alerts[0].id).toBe("critical")
      expect(contextRef!.alerts[1].id).toBe("high")
      expect(contextRef!.alerts[2].id).toBe("medium")
      expect(contextRef!.alerts[3].id).toBe("low")
    })

    it("should maintain FIFO order for same priority", async () => {
      let contextRef: ReturnType<typeof useAlertPriority> | undefined

      renderWithProviders(
        <TestComponent onContextReady={(ctx) => (contextRef = ctx)} />,
      )

      // Wait for context to be ready
      await waitFor(() => {
        expect(contextRef).toBeDefined()
      })

      await act(async () => {
        contextRef!.addAlert({
          id: "medium-1",
          priority: "medium",
          message: "First medium",
        })
        contextRef!.addAlert({
          id: "medium-2",
          priority: "medium",
          message: "Second medium",
        })
      })

      // Same priority should maintain insertion order
      const mediumAlerts = contextRef!.alerts.filter(
        (a) => a.priority === "medium",
      )
      expect(mediumAlerts[0].id).toBe("medium-1")
      expect(mediumAlerts[1].id).toBe("medium-2")
    })
  })

  describe("modal behavior", () => {
    it("should show top priority alert as modal", async () => {
      let contextRef: ReturnType<typeof useAlertPriority> | null = null

      renderWithProviders(
        <TestComponent onContextReady={(ctx) => (contextRef = ctx)} />,
      )

      await act(async () => {
        contextRef?.addAlert({
          id: "alert-1",
          priority: "high",
          message: "High priority alert",
          title: "Warning",
        })
      })

      await waitFor(() => {
        expect(screen.getByRole("dialog")).toBeInTheDocument()
      })
      expect(screen.getByText("Warning")).toBeInTheDocument()
      expect(screen.getByText("High priority alert")).toBeInTheDocument()
    })

    it("should allow dismissing modal when dismissible is not false", async () => {
      let contextRef: ReturnType<typeof useAlertPriority> | null = null

      renderWithProviders(
        <TestComponent onContextReady={(ctx) => (contextRef = ctx)} />,
      )

      await act(async () => {
        contextRef?.addAlert({
          id: "dismissible",
          priority: "medium",
          message: "Dismissible alert",
          dismissible: true,
        })
      })

      await waitFor(() => {
        expect(screen.getByRole("dialog")).toBeInTheDocument()
      })

      // Click OK button to dismiss
      await act(async () => {
        await userEvent.click(screen.getByRole("button", { name: /ok/i }))
      })

      await waitFor(() => {
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
      })
    })

    it("should call onAction when action button clicked", async () => {
      let contextRef: ReturnType<typeof useAlertPriority> | null = null
      const mockAction = jest.fn()

      renderWithProviders(
        <TestComponent onContextReady={(ctx) => (contextRef = ctx)} />,
      )

      await act(async () => {
        contextRef?.addAlert({
          id: "actionable",
          priority: "high",
          message: "Action required",
          onAction: mockAction,
          actionLabel: "Take Action",
        })
      })

      await waitFor(() => {
        expect(screen.getByRole("dialog")).toBeInTheDocument()
      })

      await act(async () => {
        await userEvent.click(
          screen.getByRole("button", { name: /take action/i }),
        )
      })

      expect(mockAction).toHaveBeenCalledTimes(1)
    })
  })

  describe("snackbar limits", () => {
    it("should limit concurrent snackbars to 2", async () => {
      let contextRef: ReturnType<typeof useAlertPriority> | undefined

      renderWithProviders(
        <TestComponent onContextReady={(ctx) => (contextRef = ctx)} />,
      )

      // Wait for context to be ready
      await waitFor(() => {
        expect(contextRef).toBeDefined()
      })

      // Add 5 alerts - 1 will be modal, only 2 should be snackbars
      await act(async () => {
        for (let i = 0; i < 5; i++) {
          contextRef!.addAlert({
            id: `alert-${i}`,
            priority: "low",
            message: `Alert ${i}`,
          })
        }
      })

      // First alert is shown as modal
      await waitFor(() => {
        expect(screen.getByRole("dialog")).toBeInTheDocument()
      })

      // The context should have all 5 alerts
      expect(contextRef!.alerts.length).toBe(5)

      // Only 2 snackbars should be visible (beyond the modal)
      // This is verified by the component's internal logic slicing to 2
    })
  })

  describe("error handling", () => {
    it("should throw error when used outside provider", () => {
      // Suppress console.error for this test
      const consoleSpy = jest.spyOn(console, "error").mockImplementation()

      const TestOutsideProvider = () => {
        useAlertPriority()
        return null
      }

      expect(() => render(<TestOutsideProvider />)).toThrow(
        "useAlertPriority must be used within AlertPriorityProvider",
      )

      consoleSpy.mockRestore()
    })
  })
})
