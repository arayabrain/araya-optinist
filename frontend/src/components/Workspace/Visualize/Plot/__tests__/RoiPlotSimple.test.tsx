import React from "react"
import { Provider } from "react-redux"

import configureStore from "redux-mock-store"
import thunk from "redux-thunk"

import { describe, it, expect, jest, beforeEach } from "@jest/globals"
import { render, screen, fireEvent } from "@testing-library/react"

import {
  RoiPlotSimple,
  RoiPlotSimpleWithLoading,
} from "components/Workspace/Visualize/Plot/RoiPlotSimple"

const mockStore = configureStore([thunk])

// Mock react-plotlyjs-ts to avoid d3-interpolate issues
jest.mock("react-plotlyjs-ts", () => ({
  __esModule: true,
  default: () => <div data-testid="plotly-chart">Plotly Chart</div>,
}))

// Mock getRoiData action - return a thunk-like function
const mockGetRoiData = jest.fn()
jest.mock("store/slice/DisplayData/DisplayDataActions", () => ({
  getRoiData: (params: {
    path: string
    workspaceId: number
    uniqueId?: string
  }) => {
    mockGetRoiData(params)
    // Return a thunk function
    return () => Promise.resolve()
  },
}))

describe("RoiPlotSimple Component", () => {
  let store: ReturnType<typeof mockStore>

  beforeEach(() => {
    mockGetRoiData.mockClear()
  })

  const renderWithProviders = (
    component: React.ReactElement,
    customStore?: ReturnType<typeof mockStore>,
  ) => {
    return render(<Provider store={customStore || store}>{component}</Provider>)
  }

  describe("Error state with retry button", () => {
    it("shows error message and retry button when error occurs", () => {
      const initialState = {
        displayData: {
          roi: {
            "/test/path": {
              type: "roi",
              data: [],
              pending: false,
              fulfilled: false,
              error: "Data not synced",
              roiUniqueList: [],
            },
          },
          loading: false,
          loadingStack: [],
        },
      }
      store = mockStore(initialState)

      renderWithProviders(
        <RoiPlotSimple filePath="/test/path" workspaceId={1} />,
      )

      // Should show error message
      expect(screen.getByText("Data not synced")).toBeDefined()

      // Should show retry button
      const retryButton = screen.getByRole("button")
      expect(retryButton).toBeDefined()
    })

    it("calls getRoiData when retry button is clicked", () => {
      const initialState = {
        displayData: {
          roi: {
            "/test/path": {
              type: "roi",
              data: [],
              pending: false,
              fulfilled: false,
              error: "Data unavailable",
              roiUniqueList: [],
            },
          },
          loading: false,
          loadingStack: [],
        },
      }
      store = mockStore(initialState)

      renderWithProviders(
        <RoiPlotSimple filePath="/test/path" workspaceId={1} />,
      )

      // Click retry button
      const retryButton = screen.getByRole("button")
      fireEvent.click(retryButton)

      // Should dispatch getRoiData action
      expect(mockGetRoiData).toHaveBeenCalledWith({
        path: "/test/path",
        workspaceId: 1,
        uniqueId: undefined,
      })
    })

    it("includes uniqueId when provided", () => {
      const initialState = {
        displayData: {
          roi: {
            "/test/path": {
              type: "roi",
              data: [],
              pending: false,
              fulfilled: false,
              error: "Data unavailable",
              roiUniqueList: [],
            },
          },
          loading: false,
          loadingStack: [],
        },
      }
      store = mockStore(initialState)

      renderWithProviders(
        <RoiPlotSimple
          filePath="/test/path"
          workspaceId={1}
          uniqueId="workflow-123"
        />,
      )

      // Click retry button
      const retryButton = screen.getByRole("button")
      fireEvent.click(retryButton)

      // Should dispatch getRoiData action with uniqueId
      expect(mockGetRoiData).toHaveBeenCalledWith({
        path: "/test/path",
        workspaceId: 1,
        uniqueId: "workflow-123",
      })
    })

    it("prevents click propagation when retry button is clicked", () => {
      const initialState = {
        displayData: {
          roi: {
            "/test/path": {
              type: "roi",
              data: [],
              pending: false,
              fulfilled: false,
              error: "Error",
              roiUniqueList: [],
            },
          },
          loading: false,
          loadingStack: [],
        },
      }
      store = mockStore(initialState)

      const mockOnClick = jest.fn()
      renderWithProviders(
        <RoiPlotSimple
          filePath="/test/path"
          workspaceId={1}
          onClick={mockOnClick}
        />,
      )

      // Click retry button - should not trigger parent onClick
      const retryButton = screen.getByRole("button")
      fireEvent.click(retryButton)

      // Parent onClick should NOT be called
      expect(mockOnClick).not.toHaveBeenCalled()
    })
  })

  describe("Loading state", () => {
    it("shows loading indicator when pending", () => {
      const initialState = {
        displayData: {
          roi: {
            "/test/path": {
              type: "roi",
              data: [],
              pending: true,
              fulfilled: false,
              error: null,
              roiUniqueList: [],
            },
          },
          loading: true,
          loadingStack: [true],
        },
      }
      store = mockStore(initialState)

      renderWithProviders(
        <RoiPlotSimple filePath="/test/path" workspaceId={1} />,
      )

      // Should show loading progress bar
      expect(screen.getByRole("progressbar")).toBeDefined()
    })
  })

  describe("No data state", () => {
    it("shows no data message when filePath is empty", () => {
      const initialState = {
        displayData: {
          roi: {},
          loading: false,
          loadingStack: [],
        },
      }
      store = mockStore(initialState)

      renderWithProviders(<RoiPlotSimple filePath="" workspaceId={1} />)

      expect(screen.getByText("No data")).toBeDefined()
    })
  })

  describe("Success state", () => {
    it("renders plotly chart when data is available", () => {
      const initialState = {
        displayData: {
          roi: {
            "/test/path": {
              type: "roi",
              data: [
                [
                  [1, 2, 3],
                  [4, 5, 6],
                ],
              ],
              pending: false,
              fulfilled: true,
              error: null,
              roiUniqueList: ["1", "2", "3", "4", "5", "6"],
            },
          },
          loading: false,
          loadingStack: [],
        },
      }
      store = mockStore(initialState)

      renderWithProviders(
        <RoiPlotSimple filePath="/test/path" workspaceId={1} />,
      )

      // Should render plotly chart
      expect(screen.getByTestId("plotly-chart")).toBeDefined()
    })
  })

  describe("Initial data fetch", () => {
    it("fetches data on mount", () => {
      const initialState = {
        displayData: {
          roi: {},
          loading: false,
          loadingStack: [],
        },
      }
      store = mockStore(initialState)

      renderWithProviders(
        <RoiPlotSimple filePath="/test/path" workspaceId={1} />,
      )

      // Should dispatch getRoiData action on mount
      expect(mockGetRoiData).toHaveBeenCalledWith({
        path: "/test/path",
        workspaceId: 1,
        uniqueId: undefined,
      })
    })

    it("fetches data with uniqueId on mount when provided", () => {
      const initialState = {
        displayData: {
          roi: {},
          loading: false,
          loadingStack: [],
        },
      }
      store = mockStore(initialState)

      renderWithProviders(
        <RoiPlotSimple
          filePath="/test/path"
          workspaceId={1}
          uniqueId="workflow-456"
        />,
      )

      // Should dispatch getRoiData action with uniqueId on mount
      expect(mockGetRoiData).toHaveBeenCalledWith({
        path: "/test/path",
        workspaceId: 1,
        uniqueId: "workflow-456",
      })
    })
  })
})

describe("RoiPlotSimpleWithLoading Component", () => {
  let store: ReturnType<typeof mockStore>

  beforeEach(() => {
    mockGetRoiData.mockClear()
  })

  const renderWithProviders = (
    component: React.ReactElement,
    customStore?: ReturnType<typeof mockStore>,
  ) => {
    return render(<Provider store={customStore || store}>{component}</Provider>)
  }

  describe("Sync indicator", () => {
    it("shows sync indicator when loading and not initialized", () => {
      const initialState = {
        displayData: {
          roi: {
            "/test/path": {
              type: "roi",
              data: [],
              pending: true,
              fulfilled: false,
              error: null,
              roiUniqueList: [],
            },
          },
          loading: true,
          loadingStack: [true],
        },
      }
      store = mockStore(initialState)

      renderWithProviders(
        <RoiPlotSimpleWithLoading filePath="/test/path" workspaceId={1} />,
      )

      // Should show sync indicator (CircularProgress)
      expect(screen.getAllByRole("progressbar").length).toBeGreaterThan(0)
    })

    it("hides sync indicator when data is loaded", () => {
      const initialState = {
        displayData: {
          roi: {
            "/test/path": {
              type: "roi",
              data: [
                [
                  [1, 2, 3],
                  [4, 5, 6],
                ],
              ],
              pending: false,
              fulfilled: true,
              error: null,
              roiUniqueList: ["1", "2", "3", "4", "5", "6"],
            },
          },
          loading: false,
          loadingStack: [],
        },
      }
      store = mockStore(initialState)

      renderWithProviders(
        <RoiPlotSimpleWithLoading filePath="/test/path" workspaceId={1} />,
      )

      // Should show the plotly chart, not sync indicator overlay
      expect(screen.getByTestId("plotly-chart")).toBeDefined()
    })

    it("passes uniqueId to inner RoiPlotSimple component", () => {
      const initialState = {
        displayData: {
          roi: {},
          loading: false,
          loadingStack: [],
        },
      }
      store = mockStore(initialState)

      renderWithProviders(
        <RoiPlotSimpleWithLoading
          filePath="/test/path"
          workspaceId={1}
          uniqueId="test-workflow-id"
        />,
      )

      // Should dispatch with uniqueId
      expect(mockGetRoiData).toHaveBeenCalledWith({
        path: "/test/path",
        workspaceId: 1,
        uniqueId: "test-workflow-id",
      })
    })
  })
})
