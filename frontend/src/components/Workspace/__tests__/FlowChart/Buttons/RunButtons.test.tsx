import React, { useState } from "react"
import { Provider } from "react-redux"

import { SnackbarProvider } from "notistack"
import configureStore from "redux-mock-store"

import {
  describe,
  it,
  beforeEach,
  jest,
  expect,
  afterEach,
} from "@jest/globals"
import "@testing-library/jest-dom"
import { render, screen, waitFor } from "@testing-library/react"
import { userEvent } from "@testing-library/user-event"

import * as StorageAlertsApi from "api/storage/StorageAlerts"
import { RunButtons } from "components/Workspace/FlowChart/Buttons/RunButtons"
import { RUN_BTN_OPTIONS } from "store/slice/Pipeline/PipelineType"

// Mock the storage alert API
jest.mock("api/storage/StorageAlerts")
const mockGetMyStorageAlertApi =
  StorageAlertsApi.getMyStorageAlertApi as jest.MockedFunction<
    typeof StorageAlertsApi.getMyStorageAlertApi
  >

const mockStore = configureStore([])

const createMockStorageAlert = (
  overrides: Partial<StorageAlertsApi.StorageAlert> = {},
): StorageAlertsApi.StorageAlert => ({
  user_id: 1,
  user_name: "Test User",
  user_email: "test@example.com",
  alert_level: "critical",
  storage_usage_bytes: 5000000000,
  storage_quota_bytes: 5368709120,
  storage_usage_percent: 93,
  timestamp: new Date().toISOString(),
  message: "Storage usage high",
  subscription_plan: "free",
  ...overrides,
})

describe("RunButtons component", () => {
  let store: ReturnType<typeof mockStore>

  const mockProps = {
    uid: "test-uid",
    runDisabled: false,
    filePathIsUndefined: false,
    algorithmNodeNotExist: false,
    handleCancelPipeline: jest.fn(),
    handleRunPipeline: jest.fn(),
    handleBatchRunPipeline: jest.fn(),
    handleRunPipelineByUid: jest.fn(),
  }

  beforeEach(() => {
    jest.clearAllMocks()
    mockGetMyStorageAlertApi.mockResolvedValue({
      has_alert: false,
      alert: null,
    })

    store = mockStore({
      pipeline: {
        run: {
          status: "StartUninitialized",
        },
      },
      currentPipeline: {
        uid: "test-uid",
      },
      runBtn: RUN_BTN_OPTIONS.RUN_ALREADY,
      workspace: {
        currentWorkspace: {
          type: 1, // WORKSPACE_TYPE.NORMAL
        },
      },
      flowElement: {
        flowNodes: [],
        flowEdges: [],
        flowPosition: { x: 0, y: 0, zoom: 1 },
        elementCoord: {},
        loading: false,
      },
      inputNode: {},
    })
  })

  afterEach(() => {
    jest.restoreAllMocks()
  })

  it("renders correctly and triggers handleRunPipelineByUid on Run button click", async () => {
    render(
      <Provider store={store}>
        <SnackbarProvider>
          <RunButtons status={"StartUninitialized"} {...mockProps} />
        </SnackbarProvider>
      </Provider>,
    )

    // Find the button using the PlayArrow icon's testid
    const runButtonIcon = screen.getByTestId("PlayArrowIcon")
    // Get the parent button from the icon
    const runButton = runButtonIcon.closest("button")

    if (runButton) {
      await userEvent.click(runButton)

      // Wait for async storage check to complete and handler to be called
      await waitFor(() => {
        expect(mockProps.handleRunPipelineByUid).toHaveBeenCalledTimes(1)
      })
    } else {
      throw new Error("Run button not found")
    }
  })

  it("disables the button after it is clicked", async () => {
    // Use state to simulate the button becoming disabled after a click
    const WrapperComponent = () => {
      const [runDisabled, setRunDisabled] = useState(false)

      const updatedProps = {
        ...mockProps,
        runDisabled,
        handleRunPipelineByUid: () => {
          // Simulate disabling the button after clicking
          setRunDisabled(true)
        },
      }

      return (
        <Provider store={store}>
          <SnackbarProvider>
            <RunButtons status={"StartUninitialized"} {...updatedProps} />
          </SnackbarProvider>
        </Provider>
      )
    }

    render(<WrapperComponent />)

    // Find the button using the PlayArrow icon's testid
    const runButtonIcon = screen.getByTestId("PlayArrowIcon")

    // Get the parent button from the icon
    const runButton = runButtonIcon.closest("button")

    if (runButton) {
      // Initially, the button should not be disabled
      expect(runButton).not.toBeDisabled()

      // Click the button
      await userEvent.click(runButton)

      // Verify that the button is disabled after the click
      expect(runButton).toBeDisabled()
    } else {
      throw new Error("Run button not found")
    }
  })

  describe("Case 39: Pre-flight Storage Check Confirmation", () => {
    it("should show confirmation dialog when storage check fails", async () => {
      mockGetMyStorageAlertApi.mockRejectedValue(new Error("Network error"))

      render(
        <Provider store={store}>
          <SnackbarProvider>
            <RunButtons status={"StartUninitialized"} {...mockProps} />
          </SnackbarProvider>
        </Provider>,
      )

      const runButtonIcon = screen.getByTestId("PlayArrowIcon")
      const runButton = runButtonIcon.closest("button")

      if (runButton) {
        await userEvent.click(runButton)

        await waitFor(() => {
          expect(screen.getByText("Storage Check Failed")).toBeTruthy()
        })

        expect(
          screen.getByText(/Unable to verify your storage quota/),
        ).toBeTruthy()
      }
    })

    it("should not run job if user cancels confirmation", async () => {
      mockGetMyStorageAlertApi.mockRejectedValue(new Error("Network error"))

      render(
        <Provider store={store}>
          <SnackbarProvider>
            <RunButtons status={"StartUninitialized"} {...mockProps} />
          </SnackbarProvider>
        </Provider>,
      )

      const runButtonIcon = screen.getByTestId("PlayArrowIcon")
      const runButton = runButtonIcon.closest("button")

      if (runButton) {
        await userEvent.click(runButton)

        await waitFor(() => {
          expect(screen.getByText("Storage Check Failed")).toBeTruthy()
        })

        const cancelButton = screen.getByRole("button", { name: /Cancel/i })
        await userEvent.click(cancelButton)

        expect(mockProps.handleRunPipelineByUid).not.toHaveBeenCalled()
      }
    })

    it("should run job if user proceeds despite failed check", async () => {
      mockGetMyStorageAlertApi.mockRejectedValue(new Error("Network error"))

      render(
        <Provider store={store}>
          <SnackbarProvider>
            <RunButtons status={"StartUninitialized"} {...mockProps} />
          </SnackbarProvider>
        </Provider>,
      )

      const runButtonIcon = screen.getByTestId("PlayArrowIcon")
      const runButton = runButtonIcon.closest("button")

      if (runButton) {
        await userEvent.click(runButton)

        await waitFor(() => {
          expect(screen.getByText("Storage Check Failed")).toBeTruthy()
        })

        const proceedButton = screen.getByRole("button", {
          name: /Proceed Anyway/i,
        })
        await userEvent.click(proceedButton)

        await waitFor(() => {
          expect(mockProps.handleRunPipelineByUid).toHaveBeenCalled()
        })
      }
    })

    it("should block job when storage is at danger level", async () => {
      mockGetMyStorageAlertApi.mockResolvedValue({
        has_alert: true,
        alert: createMockStorageAlert({
          alert_level: "danger",
          storage_usage_percent: 105,
        }),
      })

      render(
        <Provider store={store}>
          <SnackbarProvider>
            <RunButtons status={"StartUninitialized"} {...mockProps} />
          </SnackbarProvider>
        </Provider>,
      )

      const runButtonIcon = screen.getByTestId("PlayArrowIcon")
      const runButton = runButtonIcon.closest("button")

      if (runButton) {
        await userEvent.click(runButton)

        await waitFor(() => {
          expect(mockProps.handleRunPipelineByUid).not.toHaveBeenCalled()
        })

        // Should not show confirmation dialog for danger level
        expect(screen.queryByText("Storage Check Failed")).toBeFalsy()
      }
    })

    it("should proceed with warning for critical level", async () => {
      mockGetMyStorageAlertApi.mockResolvedValue({
        has_alert: true,
        alert: createMockStorageAlert({
          alert_level: "critical",
          storage_usage_percent: 95,
        }),
      })

      render(
        <Provider store={store}>
          <SnackbarProvider>
            <RunButtons status={"StartUninitialized"} {...mockProps} />
          </SnackbarProvider>
        </Provider>,
      )

      const runButtonIcon = screen.getByTestId("PlayArrowIcon")
      const runButton = runButtonIcon.closest("button")

      if (runButton) {
        await userEvent.click(runButton)

        await waitFor(() => {
          expect(mockProps.handleRunPipelineByUid).toHaveBeenCalled()
        })

        // Should not show confirmation dialog for critical level
        expect(screen.queryByText("Storage Check Failed")).toBeFalsy()
      }
    })
  })
})
