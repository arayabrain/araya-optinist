import { describe, expect, test } from "@jest/globals"

import {
  getHiddenOutputs,
  putHiddenOutputs,
} from "store/slice/Dataview/DataviewActions"
import reducer, { initialState } from "store/slice/Dataview/DataviewSlice"
import { DataviewType } from "store/slice/Dataview/DataviewType"

const record = (id: number, hidden_outputs?: string[]): DataviewType => ({
  id,
  uid: `uid${id}`,
  name: `record ${id}`,
  owner: {},
  workspace: { id: 1 },
  thumbnails: {},
  analyzed_at: "",
  created_at: "",
  updated_at: "",
  hidden_outputs,
})

const state = {
  ...initialState,
  data: {
    ...initialState.data,
    private: {
      ...initialState.data.private,
      items: [record(1), record(2, ["1/uid2/a"])],
    },
  },
}

const hidden = (next: typeof state) =>
  next.data.private.items.map((item) => item.hidden_outputs)

describe.each([
  [
    "getHiddenOutputs",
    (list: string[], id: number) =>
      getHiddenOutputs.fulfilled(list, "request", { id }),
  ],
  [
    "putHiddenOutputs",
    (list: string[], id: number) =>
      putHiddenOutputs.fulfilled(list, "request", {
        id,
        path: "p",
        hidden: true,
      }),
  ],
])("%s.fulfilled", (_, fulfilled) => {
  test("writes the returned list onto that record only", () => {
    const next = reducer(state, fulfilled(["1/uid1/x"], 1))
    expect(hidden(next)).toEqual([["1/uid1/x"], ["1/uid2/a"]])
  })

  test("leaves the list alone for a record it does not hold", () => {
    const next = reducer(state, fulfilled(["1/uid9/x"], 9))
    expect(hidden(next)).toEqual([undefined, ["1/uid2/a"]])
  })
})
