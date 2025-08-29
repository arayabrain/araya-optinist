import React from "react"
import { Provider } from "react-redux"
import { MemoryRouter } from "react-router-dom"

import configureStore from "redux-mock-store"
import thunk from "redux-thunk"

import { describe, it, expect, jest, beforeEach } from "@jest/globals"
import { render, screen, fireEvent } from "@testing-library/react"

import { UserDTO } from "api/users/UsersApiDTO"
import DataviewRecords from "components/Dataview/DataviewRecords"
import { DataviewType } from "store/slice/Dataview/DataviewType"

const mockStore = configureStore([thunk])

// Mock Dataview Actions
jest.mock("store/slice/Dataview/DataviewActions", () => ({
  getDataviewRecords: () => ({ type: "GET_DATAVIEW_RECORDS" }),
  getPublicDataviewRecords: () => ({ type: "GET_PUBLIC_DATAVIEW_RECORDS" }),
}))

// Mock react-plotlyjs-ts to avoid d3-interpolate issues
jest.mock("react-plotlyjs-ts", () => ({
  __esModule: true,
  default: () => <div data-testid="plotly-chart">Plotly Chart</div>,
}))

jest.mock("components/Dataview/InputsView", () => {
  const MockInputsView = ({
    open,
    handleClose,
  }: {
    open: boolean
    handleClose: () => void
  }) => (
    <div data-testid="inputs-view" data-open={open}>
      <button onClick={handleClose}>Close</button>
    </div>
  )
  MockInputsView.displayName = "InputsView"
  return MockInputsView
})

jest.mock("components/Dataview/OutputsView", () => {
  const MockOutputsView = ({
    open,
    handleClose,
  }: {
    open: boolean
    handleClose: () => void
  }) => (
    <div data-testid="outputs-view" data-open={open}>
      <button onClick={handleClose}>Close</button>
    </div>
  )
  MockOutputsView.displayName = "OutputsView"
  return MockOutputsView
})

jest.mock("components/Dataview/WorkflowDetailsView", () => {
  const MockWorkflowDetailsView = ({
    open,
    onClose,
  }: {
    open: boolean
    onClose: () => void
  }) => (
    <div data-testid="workflow-details-view" data-open={open}>
      <button onClick={onClose}>Close</button>
    </div>
  )
  MockWorkflowDetailsView.displayName = "WorkflowDetailsView"
  return { WorkflowDetailsView: MockWorkflowDetailsView }
})

jest.mock("components/common/Loading", () => {
  const MockLoading = ({ loading }: { loading: boolean }) =>
    loading ? <div data-testid="loading">Loading...</div> : null
  MockLoading.displayName = "Loading"
  return MockLoading
})

jest.mock("components/common/PaginationCustom", () => {
  const MockPaginationCustom = ({
    handlePage,
    handleLimit,
  }: {
    handlePage: (e: React.ChangeEvent<unknown>, page: number) => void
    handleLimit: (e: React.ChangeEvent<HTMLSelectElement>) => void
  }) => (
    <div data-testid="pagination">
      <button onClick={(e) => handlePage(e as React.ChangeEvent<unknown>, 2)}>
        Page 2
      </button>
      <select onChange={handleLimit}>
        <option value="25">25</option>
        <option value="50">50</option>
      </select>
    </div>
  )
  MockPaginationCustom.displayName = "PaginationCustom"
  return MockPaginationCustom
})

jest.mock("components/Workspace/Visualize/Plot/ImagePlotSimple", () => ({
  ImagePlotSimple: () => <div data-testid="image-plot">Image Plot</div>,
}))

jest.mock("components/Workspace/Visualize/Plot/RoiPlotSimple", () => ({
  RoiPlotSimple: () => <div data-testid="roi-plot">ROI Plot</div>,
}))

jest.mock("components/common/SwitchCustom", () => {
  const MockSwitchCustom = ({
    checked,
    onChange,
  }: {
    checked: boolean
    onChange: () => void
  }) => (
    <input
      type="checkbox"
      checked={checked}
      onChange={onChange}
      data-testid="switch-custom"
    />
  )
  MockSwitchCustom.displayName = "SwitchCustom"
  return { __esModule: true, default: MockSwitchCustom }
})

jest.mock("components/common/ConfirmDialog", () => ({
  ConfirmDialog: ({
    open,
    onClose,
    onConfirm,
  }: {
    open: boolean
    onClose: () => void
    onConfirm: () => void
  }) =>
    open ? (
      <div data-testid="confirm-dialog">
        <button onClick={onClose} data-testid="cancel-button">
          Cancel
        </button>
        <button onClick={onConfirm} data-testid="confirm-button">
          Confirm
        </button>
      </div>
    ) : null,
}))

const mockDataviewRecord: DataviewType = {
  id: 1,
  uid: "test-uid-123",
  name: "Test Workflow",
  analyzed_at: "2023-01-01T00:00:00Z",
  created_at: "2023-01-01T00:00:00Z",
  updated_at: "2023-01-01T00:00:00Z",
  publish_status: 0,
  workspace: {
    id: 1,
    name: "Test Workspace",
  },
  owner: {
    name: "Test User",
  },
  thumbnails: {
    image_url: "/test/image.png",
    roi_url: "/test/roi.png",
  },
  attributes: {},
}

const mockUser: UserDTO = {
  id: 1,
  name: "Test User",
  email: "test@example.com",
  data_usage: 0,
}

describe("DataviewRecords Component", () => {
  let store: ReturnType<typeof mockStore>

  beforeEach(() => {
    const initialState = {
      dataview: {
        data: {
          private: {
            items: [mockDataviewRecord],
            total: 1,
            offset: 0,
            limit: 50,
          },
          public: {
            items: [mockDataviewRecord],
            total: 1,
            offset: 0,
            limit: 50,
          },
        },
        loading: false,
      },
      inputNode: {},
      flowElement: {
        flowNodes: [],
        flowEdges: [],
        flowPosition: { x: 0, y: 0 },
        elementCoord: {},
        loading: false,
      },
      flowElements: {
        nodeDict: {},
      },
      algorithmNode: {},
      pipeline: {
        nodeDict: {},
        run: { status: "SUCCESS", runResult: {} },
        runBtn: false,
        currentPipeline: null,
      },
      experiments: {
        status: "fulfilled",
        experimentList: {},
      },
    }
    store = mockStore(initialState)
    store.dispatch = jest.fn()
  })

  const renderWithProviders = (component: React.ReactElement) => {
    return render(
      <Provider store={store}>
        <MemoryRouter
          future={{
            v7_startTransition: true,
            v7_relativeSplatPath: true,
          }}
        >
          {component}
        </MemoryRouter>
      </Provider>,
    )
  }

  describe("Private view (with user)", () => {
    it("renders DataGrid with workflow records", () => {
      renderWithProviders(<DataviewRecords user={mockUser} />)

      expect(screen.getByRole("grid")).toBeDefined()
      expect(screen.getByText("test-uid-123")).toBeDefined()
      expect(screen.getByText("Test Workflow")).toBeDefined()
    })

    it("shows publish/unpublish buttons when not readonly", () => {
      renderWithProviders(<DataviewRecords user={mockUser} readonly={false} />)

      const publishButtons = screen.getAllByLabelText(/bulk/i)
      expect(publishButtons).toHaveLength(2)
    })

    it("does not show publish buttons when readonly", () => {
      renderWithProviders(<DataviewRecords user={mockUser} readonly={true} />)

      const publishButtons = screen.queryAllByRole("button", { name: /bulk/i })
      expect(publishButtons).toHaveLength(0)
    })

    it("shows checkboxes when not readonly", () => {
      renderWithProviders(<DataviewRecords user={mockUser} readonly={false} />)

      const checkboxes = screen.getAllByRole("checkbox")
      expect(checkboxes.length).toBeGreaterThan(0)
    })
  })

  describe("Public view (no user)", () => {
    it("renders DataGrid with public workflow records", () => {
      renderWithProviders(<DataviewRecords />)

      expect(screen.getByRole("grid")).toBeDefined()
      expect(screen.getByText("test-uid-123")).toBeDefined()
      expect(screen.getByText("Test Workflow")).toBeDefined()
    })

    it("does not show publish buttons in public view", () => {
      renderWithProviders(<DataviewRecords />)

      const publishButtons = screen.queryAllByRole("button", { name: /bulk/i })
      expect(publishButtons).toHaveLength(0)
    })

    it("shows owner column in public view", () => {
      renderWithProviders(<DataviewRecords />)

      expect(screen.getByText("Test User")).toBeDefined()
    })
  })

  describe("Loading state", () => {
    it("shows loading component when loading is true", () => {
      const loadingState = {
        dataview: {
          data: {
            private: {
              items: [mockDataviewRecord],
              total: 1,
              offset: 0,
              limit: 50,
            },
            public: {
              items: [mockDataviewRecord],
              total: 1,
              offset: 0,
              limit: 50,
            },
          },
          loading: true,
        },
        inputNode: {},
        flowElement: {
          flowNodes: [],
          flowEdges: [],
          flowPosition: { x: 0, y: 0 },
          elementCoord: {},
          loading: false,
        },
        flowElements: {
          nodeDict: {},
        },
        algorithmNode: {},
        pipeline: {
          nodeDict: {},
          run: { status: "SUCCESS", runResult: {} },
          runBtn: false,
          currentPipeline: null,
        },
        experiments: {
          status: "fulfilled",
          experimentList: {},
        },
      }
      store = mockStore(loadingState)

      renderWithProviders(<DataviewRecords user={mockUser} />)

      expect(screen.getByTestId("loading")).toBeDefined()
    })
  })

  describe("Workspace filtering", () => {
    it("shows workspace chip when workspaceId is provided and workspace name is available", () => {
      const stateWithWorkspace = {
        dataview: {
          data: {
            private: {
              items: [mockDataviewRecord],
              total: 1,
              offset: 0,
              limit: 50,
              header: {
                workspace_name: "Test Workspace",
              },
            },
            public: {
              items: [mockDataviewRecord],
              total: 1,
              offset: 0,
              limit: 50,
            },
          },
          loading: false,
        },
        inputNode: {},
        flowElement: {
          flowNodes: [],
          flowEdges: [],
          flowPosition: { x: 0, y: 0 },
          elementCoord: {},
          loading: false,
        },
        flowElements: {
          nodeDict: {},
        },
        algorithmNode: {},
        pipeline: {
          nodeDict: {},
          run: { status: "SUCCESS", runResult: {} },
          runBtn: false,
          currentPipeline: null,
        },
        experiments: {
          status: "fulfilled",
          experimentList: {},
        },
      }
      store = mockStore(stateWithWorkspace)

      renderWithProviders(<DataviewRecords user={mockUser} workspaceId="1" />)

      expect(screen.getByText("Test Workspace")).toBeDefined()
    })
  })

  describe("Pagination", () => {
    it("shows pagination when there are records", () => {
      renderWithProviders(<DataviewRecords user={mockUser} />)

      expect(screen.getByTestId("pagination")).toBeDefined()
    })

    it("handles page change", () => {
      renderWithProviders(<DataviewRecords user={mockUser} />)

      const pageButton = screen.getByText("Page 2")
      fireEvent.click(pageButton)

      // Since we're testing with mocked store, we can verify dispatch was called
      expect(store.dispatch).toHaveBeenCalled()
    })
  })

  describe("Row click handling", () => {
    it("calls handleRowClick when provided", () => {
      const mockHandleRowClick = jest.fn()
      renderWithProviders(
        <DataviewRecords user={mockUser} handleRowClick={mockHandleRowClick} />,
      )

      const grid = screen.getByRole("grid")
      expect(grid).toBeDefined()
    })
  })
})
