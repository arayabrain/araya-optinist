import React from "react"
import { Provider } from "react-redux"

import configureStore from "redux-mock-store"

import { describe, it, expect, jest, beforeEach } from "@jest/globals"
import { render, screen, fireEvent } from "@testing-library/react"

import { VisualizationItemData } from "components/Dataview/BaseNodesView"
import InputsView from "components/Dataview/InputsView"

const mockStore = configureStore([])

jest.mock("components/Dataview/BaseNodesView", () => ({
  __esModule: true,
  default: ({
    open,
    title,
    data,
    renderData,
    emptyMessage,
    handleClose,
  }: {
    open: boolean
    title: string
    data: unknown[]
    renderData: () => React.ReactElement[]
    emptyMessage: string
    handleClose: () => void
  }) => (
    <div data-testid="base-nodes-view" data-open={open}>
      <div data-testid="title">{title}</div>
      <div data-testid="data-length">{data.length}</div>
      <div data-testid="empty-message">{emptyMessage}</div>
      {data.length > 0 ? renderData() : <div>{emptyMessage}</div>}
      <button onClick={handleClose} data-testid="close-button">
        Close
      </button>
    </div>
  ),
  renderVisualizationItems: (items: { itemKey: string; title: string }[]) =>
    items.map((item, index) => (
      <div key={index} data-testid={`visualization-item-${item.itemKey}`}>
        {item.title}
      </div>
    )),
  useDataviewVisualizationCleanup: (open: boolean) => {
    mockUseDataviewVisualizationCleanup(open)
  },
}))

// Mock functions for testing
const mockUseDataviewVisualizationCleanup = jest.fn()

const mockVisualizationItem: VisualizationItemData = {
  nodeId: "node-1",
  filePath: "/test/input.csv",
  dataType: "CSV" as VisualizationItemData["dataType"],
  title: "Input Data 1",
  subtitle: "CSV File",
  itemKey: "input-1",
}

describe("InputsView Component", () => {
  let store: ReturnType<typeof mockStore>

  beforeEach(() => {
    mockUseDataviewVisualizationCleanup.mockClear()
    const initialState = {
      dataview: {
        data: {
          private: {
            items: [],
            total: 0,
            offset: 0,
            limit: 50,
          },
          public: {
            items: [],
            total: 0,
            offset: 0,
            limit: 50,
          },
        },
        loading: false,
      },
      inputNode: {
        node_1: {
          selectedFilePath: "/test/input.csv",
          fileType: "CSV",
          filePath: "/test/input.csv",
        },
      },
      flowElement: {
        flowNodes: [
          {
            id: "node_1",
            data: {
              label: "Input Node 1",
              type: "input",
            },
          },
        ],
        flowEdges: [],
        flowPosition: { x: 0, y: 0 },
        elementCoord: {},
        loading: false,
      },
      flowElements: {
        nodeDict: {
          node_1: {
            id: "node_1",
            name: "Input Node 1",
          },
        },
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
  })

  const defaultProps = {
    open: true,
    is_public: false,
    workspaceId: 123,
    uid: "test-uid-456",
    handleClose: jest.fn(),
  }

  const renderWithProviders = (props = {}) => {
    return render(
      <Provider store={store}>
        <InputsView {...defaultProps} {...props} />
      </Provider>,
    )
  }

  describe("Component rendering", () => {
    it("renders with correct title", () => {
      renderWithProviders()

      const titleElement = screen.getByTestId("title")
      expect(titleElement.textContent).toBe("Workflow Inputs")
    })

    it("renders with correct empty message", () => {
      const emptyState = {
        dataview: {
          data: {
            private: { items: [], total: 0, offset: 0, limit: 50 },
            public: { items: [], total: 0, offset: 0, limit: 50 },
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
        flowElements: { nodeDict: {} },
        algorithmNode: {},
        pipeline: { nodeDict: {} },
      }
      store = mockStore(emptyState)

      renderWithProviders()

      const emptyMessageElement = screen.getByTestId("empty-message")
      expect(emptyMessageElement.textContent).toBe("No input data available")
    })

    it("passes correct props to BaseNodesView", () => {
      renderWithProviders()

      const baseView = screen.getByTestId("base-nodes-view")
      expect(baseView.getAttribute("data-open")).toBe("true")
      const dataLengthElement = screen.getByTestId("data-length")
      expect(dataLengthElement.textContent).toBe("1")
    })
  })

  describe("Data rendering", () => {
    it("renders visualization items when data is available", () => {
      renderWithProviders()

      const visualizationItem = screen.getByTestId("visualization-item-node_1")
      expect(visualizationItem).toBeDefined()
      expect(screen.getByText("Input Node 1")).toBeDefined()
    })

    it("renders empty message when no data is available", () => {
      const emptyState = {
        dataview: {
          data: {
            private: { items: [], total: 0, offset: 0, limit: 50 },
            public: { items: [], total: 0, offset: 0, limit: 50 },
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
        flowElements: { nodeDict: {} },
        algorithmNode: {},
        pipeline: { nodeDict: {} },
      }
      store = mockStore(emptyState)

      renderWithProviders()

      const emptyMessageElement = screen.getByTestId("empty-message")
      expect(emptyMessageElement.textContent).toBe("No input data available")
      const dataLengthElement = screen.getByTestId("data-length")
      expect(dataLengthElement.textContent).toBe("0")
    })

    it("renders multiple visualization items", () => {
      const multipleItemsState = {
        dataview: {
          data: {
            private: { items: [], total: 0, offset: 0, limit: 50 },
            public: { items: [], total: 0, offset: 0, limit: 50 },
          },
          loading: false,
        },
        inputNode: {
          node_1: {
            selectedFilePath: "/test/input1.csv",
            fileType: "CSV",
            filePath: "/test/input1.csv",
          },
          node_2: {
            selectedFilePath: "/test/input2.csv",
            fileType: "CSV",
            filePath: "/test/input2.csv",
          },
        },
        flowElement: {
          flowNodes: [
            {
              id: "node_1",
              data: { label: "Input Node 1", type: "input" },
            },
            {
              id: "node_2",
              data: { label: "Input Node 2", type: "input" },
            },
          ],
          flowEdges: [],
          flowPosition: { x: 0, y: 0 },
          elementCoord: {},
          loading: false,
        },
        flowElements: {
          nodeDict: {
            node_1: { id: "node_1", name: "Input Node 1" },
            node_2: { id: "node_2", name: "Input Node 2" },
          },
        },
        algorithmNode: {},
        pipeline: { nodeDict: {} },
      }
      store = mockStore(multipleItemsState)

      renderWithProviders()

      expect(screen.getByTestId("visualization-item-node_1")).toBeDefined()
      expect(screen.getByTestId("visualization-item-node_2")).toBeDefined()
      expect(screen.getByText("Input Node 1")).toBeDefined()
      expect(screen.getByText("Input Node 2")).toBeDefined()
    })
  })

  describe("Component state", () => {
    it("handles closed state", () => {
      renderWithProviders({ open: false })

      const baseView = screen.getByTestId("base-nodes-view")
      expect(baseView.getAttribute("data-open")).toBe("false")
    })

    it("handles public view", () => {
      renderWithProviders({ is_public: true })

      const baseView = screen.getByTestId("base-nodes-view")
      expect(baseView).toBeDefined()
    })

    it("handles missing workspaceId", () => {
      renderWithProviders({ workspaceId: undefined })

      const baseView = screen.getByTestId("base-nodes-view")
      expect(baseView).toBeDefined()
    })

    it("handles missing uid", () => {
      renderWithProviders({ uid: undefined })

      const baseView = screen.getByTestId("base-nodes-view")
      expect(baseView).toBeDefined()
    })
  })

  describe("User interactions", () => {
    it("calls handleClose when close button is clicked", () => {
      const mockHandleClose = jest.fn()
      renderWithProviders({ handleClose: mockHandleClose })

      fireEvent.click(screen.getByTestId("close-button"))

      expect(mockHandleClose).toHaveBeenCalledTimes(1)
    })
  })

  describe("Hook usage", () => {
    it("calls useDataviewVisualizationCleanup with open state", () => {
      renderWithProviders({ open: true })

      expect(mockUseDataviewVisualizationCleanup).toHaveBeenCalledWith(true)
    })

    it("calls useDataviewVisualizationCleanup with closed state", () => {
      renderWithProviders({ open: false })

      expect(mockUseDataviewVisualizationCleanup).toHaveBeenCalledWith(false)
    })
  })

  describe("Redux integration", () => {
    it("selects input visualization items from store", () => {
      renderWithProviders()

      const dataLengthElement = screen.getByTestId("data-length")
      expect(dataLengthElement.textContent).toBe("1")
    })

    it("handles empty visualization items from store", () => {
      const emptyState = {
        dataview: {
          data: {
            private: { items: [], total: 0, offset: 0, limit: 50 },
            public: { items: [], total: 0, offset: 0, limit: 50 },
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
        flowElements: { nodeDict: {} },
        algorithmNode: {},
        pipeline: { nodeDict: {} },
      }
      store = mockStore(emptyState)

      renderWithProviders()

      const dataLengthElement = screen.getByTestId("data-length")
      expect(dataLengthElement.textContent).toBe("0")
    })
  })
})
