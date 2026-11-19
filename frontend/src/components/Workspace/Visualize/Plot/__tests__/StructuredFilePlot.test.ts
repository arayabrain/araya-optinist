import { describe, expect, it, jest } from "@jest/globals"

import { reducedNote } from "components/Workspace/Visualize/Plot/StructuredFilePlot"

jest.mock("react-plotlyjs-ts", () => () => null)

describe("reducedNote", () => {
  it("says nothing when the whole array was sent", () => {
    expect(reducedNote(3, 2, [3, 2])).toBeNull()
  })

  it("names the frames kept", () => {
    expect(reducedNote(3984, 502, [81904, 502])).toBe(
      "Showing the first 3984 of 81904 frames",
    )
  })

  it("names the frames and the columns kept", () => {
    expect(reducedNote(2000, 1000, [81904, 5000])).toBe(
      "Showing the first 2000 of 81904 frames and 1000 of 5000 columns",
    )
  })

  it("says nothing without a shape", () => {
    expect(reducedNote(3, 2, undefined)).toBeNull()
  })
})
