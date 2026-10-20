import { describe, expect, test } from "@jest/globals"

import reducer, {
  setStructuredInputNodeParam,
} from "store/slice/InputNode/InputNodeSlice"
import { InputNode } from "store/slice/InputNode/InputNodeType"
import { selectRunPostData } from "store/slice/Run/RunSelectors"
import {
  importWorkflowConfig,
  reproduceWorkflow,
} from "store/slice/Workflow/WorkflowActions"
import { RootState, store } from "store/store"

const inputNode = (fileType: string, param: unknown, extra = {}) => ({
  id: fileType,
  data: {
    type: "input",
    label: fileType,
    fileType,
    path: `${fileType}-file`,
    param,
    ...extra,
  },
  position: { x: 0, y: 0 },
})

const savedNodes = {
  hdf5: inputNode(
    "hdf5",
    { transpose: true, dataType: "behavior", unknown: 1 },
    { hdf5Path: "g/data" },
  ),
  matlab: inputNode(
    "matlab",
    { transpose: true, dataType: "behavior" },
    { matPath: "x" },
  ),
  legacy: { ...inputNode("hdf5", {}), id: "legacy" },
  csv: inputNode("csv", { transpose: true, setHeader: 0, extra: true }),
}

describe.each([importWorkflowConfig, reproduceWorkflow])(
  "%p keeps the input settings",
  (thunk) => {
    const state = reducer(undefined, {
      type: thunk.fulfilled.type,
      payload: { nodeDict: savedNodes },
    }) as InputNode

    test.each(["hdf5", "matlab"])("%s keeps transpose and dataType", (id) => {
      expect(state[id].param).toEqual({ transpose: true, dataType: "behavior" })
    })

    test("a node saved before the settings gets the defaults", () => {
      expect(state.legacy.param).toEqual({ transpose: false, dataType: "auto" })
    })

    test("csv merge is unchanged and unknown keys are dropped", () => {
      expect(state.csv.param).toEqual({
        setHeader: 0,
        setIndex: false,
        transpose: true,
      })
    })
  },
)

test("setStructuredInputNodeParam only touches HDF5 and MAT nodes", () => {
  const loaded = reducer(undefined, {
    type: importWorkflowConfig.fulfilled.type,
    payload: { nodeDict: savedNodes },
  })
  const param = { transpose: false, dataType: "auto" as const }

  const hdf5 = reducer(
    loaded,
    setStructuredInputNodeParam({ nodeId: "hdf5", param }),
  )
  const csv = reducer(
    loaded,
    setStructuredInputNodeParam({ nodeId: "csv", param }),
  )

  expect(hdf5.hdf5.param).toEqual(param)
  expect(csv.csv.param).toEqual(loaded.csv.param)
})

test("the run payload carries the HDF5 and MAT settings", () => {
  const nodes = [savedNodes.hdf5, savedNodes.matlab]
  const state = {
    ...store.getState(),
    flowElement: { ...store.getState().flowElement, flowNodes: nodes },
    inputNode: reducer(undefined, {
      type: importWorkflowConfig.fulfilled.type,
      payload: { nodeDict: savedNodes },
    }),
  } as unknown as RootState

  const { nodeDict } = selectRunPostData(state)

  for (const id of ["hdf5", "matlab"]) {
    expect(nodeDict[id].data?.param).toEqual({
      transpose: true,
      dataType: "behavior",
    })
  }
})
