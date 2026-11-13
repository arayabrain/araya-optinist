/* eslint-disable no-undef */
import "@testing-library/jest-dom"
import { Provider } from "react-redux"

import configureMockStore from "redux-mock-store"

import { describe, it } from "@jest/globals"
import { fireEvent, render, screen } from "@testing-library/react"

import { StructureItemSelectDialog } from "components/Workspace/FlowChart/Dialog/StructureItemSelectDialog"
import { FileNodeConfig } from "components/Workspace/FlowChart/FlowChartNode/BaseStructuredFileNode"

const config: FileNodeConfig = {
  fileType: "hdf5",
  handleId: "hdf5",
  handleType: "HDF5Data",
  treeKeyPrefix: "hdf5",
  selectFilePath: () => () => "input.h5",
  selectStructurePath: () => () => "/data/traces",
  setStructurePath: (payload) => ({ type: "setStructurePath", payload }),
  getTree: () => ({ type: "getTree" }) as never,
  selectTree: () => () => [],
  selectIsLoading: () => () => false,
}

describe("StructureItemSelectDialog", () => {
  it("keeps the saved dataset path when only the switches change", () => {
    const store = configureMockStore([])({
      inputNode: { node: { param: { transpose: false, dataType: "auto" } } },
      workspace: { currentWorkspace: { workspaceId: 1 } },
    })
    render(
      <Provider store={store}>
        <StructureItemSelectDialog
          nodeId="node"
          open
          setOpen={jest.fn()}
          config={config}
        />
      </Provider>,
    )

    fireEvent.click(screen.getByLabelText("Transpose"))
    fireEvent.click(screen.getByRole("button", { name: "OK" }))

    expect(store.getActions().filter(({ type }) => type !== "getTree")).toEqual(
      [
        {
          type: "setStructurePath",
          payload: { nodeId: "node", path: "/data/traces" },
        },
        expect.objectContaining({
          payload: {
            nodeId: "node",
            param: { transpose: true, dataType: "auto" },
          },
        }),
      ],
    )
  })
})
