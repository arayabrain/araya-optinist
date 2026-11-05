import React from "react"
import { Provider } from "react-redux"

import configureStore from "redux-mock-store"
import thunk from "redux-thunk"

import { describe, it, expect, jest, beforeEach } from "@jest/globals"
import { act, render, screen, fireEvent } from "@testing-library/react"

import { RoiPlotSimple } from "components/Workspace/Visualize/Plot/RoiPlotSimple"

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
  SYNC_IN_PROGRESS_MESSAGE: "Syncing from cloud storage...",
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

    it("hides retry button when error contains 'not found'", () => {
      const initialState = {
        displayData: {
          roi: {
            "/test/path": {
              type: "roi",
              data: [],
              pending: false,
              fulfilled: false,
              error:
                "Output file not found. Analysis may not have generated this file.",
              errorStatus: 404,
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
      expect(
        screen.getByText(
          "Output file not found. Analysis may not have generated this file.",
        ),
      ).toBeDefined()

      // Should NOT show retry button for not-found errors
      expect(screen.queryByRole("button")).toBeNull()
    })

    it("shows retry button for syncing errors", () => {
      const initialState = {
        displayData: {
          roi: {
            "/test/path": {
              type: "roi",
              data: [],
              pending: false,
              fulfilled: false,
              error: "Syncing from cloud storage...",
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

      // Should show syncing message
      expect(screen.getByText("Syncing from cloud storage...")).toBeDefined()

      // Should show retry button (syncing is retryable)
      const retryButton = screen.getByRole("button")
      expect(retryButton).toBeDefined()
    })

    it("hides retry button for 404 errors based on errorStatus", () => {
      const initialState = {
        displayData: {
          roi: {
            "/test/path": {
              type: "roi",
              data: [],
              pending: false,
              fulfilled: false,
              error: "Output file not found",
              errorStatus: 404,
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
      expect(screen.getByText("Output file not found")).toBeDefined()

      // Should NOT show retry button for 404 errors
      expect(screen.queryByRole("button")).toBeNull()
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

    // The fetch is dispatched from an effect, so the first painted frame has no
    // store entry at all and used to fall through to the no-data branch.
    it("shows the loading indicator before the fetch registers in the store", () => {
      store = mockStore({
        displayData: { roi: {}, loading: false, loadingStack: [] },
      })

      renderWithProviders(
        <RoiPlotSimple filePath="/test/path" workspaceId={1} />,
      )

      expect(
        screen.getByRole("progressbar", { name: "Loading thumbnail" }),
      ).toBeDefined()
      expect(
        screen.getByLabelText("May need to sync from cloud storage"),
      ).toBeDefined()
      expect(screen.queryByText("No ROI data")).toBeNull()
    })

    // Without a workspaceId the effect never dispatches, so an indicator would spin forever.
    it("shows no data rather than a stuck indicator when workspaceId is missing", () => {
      store = mockStore({
        displayData: { roi: {}, loading: false, loadingStack: [] },
      })

      renderWithProviders(
        <RoiPlotSimple filePath="/test/path" workspaceId={0} />,
      )

      expect(screen.queryByRole("progressbar")).toBeNull()
      expect(screen.getByText("No ROI data")).toBeDefined()
    })

    // An all-zero mask fulfils with maxIndex 0, which is the production route
    // into the no-data branch.
    it("shows no data once a fetch resolves with an empty mask", () => {
      store = mockStore({
        displayData: {
          roi: {
            "/test/path": {
              type: "roi",
              data: [
                [
                  [0, 0],
                  [0, 0],
                ],
              ],
              pending: false,
              fulfilled: true,
              error: null,
              roiUniqueList: [],
            },
          },
          loading: false,
          loadingStack: [],
        },
      })

      renderWithProviders(
        <RoiPlotSimple filePath="/test/path" workspaceId={1} />,
      )

      expect(screen.queryByRole("progressbar")).toBeNull()
      expect(screen.getByText("No ROI data")).toBeDefined()
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

  describe("Store entry wiped after the first fetch", () => {
    const entry = (overrides: object) => ({
      type: "roi",
      data: [],
      pending: false,
      fulfilled: false,
      error: null,
      roiUniqueList: [],
      ...overrides,
    })
    let roi: Record<string, unknown>
    let liveStore: ReturnType<typeof mockStore>

    const setRoi = (next: Record<string, unknown>) =>
      act(() => {
        roi = next
        liveStore.dispatch({ type: "test/setRoi" })
      })

    beforeEach(() => {
      roi = {}
      liveStore = mockStore(() => ({
        displayData: { roi, loading: false, loadingStack: [] },
      }))
      renderWithProviders(
        <RoiPlotSimple filePath="/test/path" workspaceId={1} />,
        liveStore,
      )
      setRoi({ "/test/path": entry({ pending: true }) })
    })

    it("fetches again and leaves the indicator once the data arrives", () => {
      expect(mockGetRoiData).toHaveBeenCalledTimes(1)

      setRoi({})
      expect(mockGetRoiData).toHaveBeenCalledTimes(2)

      setRoi({ "/test/path": entry({ pending: true }) })
      setRoi({
        "/test/path": entry({
          data: [
            [
              [1, 2],
              [3, 4],
            ],
          ],
          fulfilled: true,
        }),
      })
      expect(mockGetRoiData).toHaveBeenCalledTimes(2)
      expect(screen.queryByRole("progressbar")).toBeNull()
      expect(screen.getByTestId("plotly-chart")).toBeDefined()
    })

    it("does not fetch again after a failed fetch", () => {
      setRoi({ "/test/path": entry({ error: "Data not synced" }) })

      expect(mockGetRoiData).toHaveBeenCalledTimes(1)
      expect(screen.getByText("Data not synced")).toBeDefined()
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
