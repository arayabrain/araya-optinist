import { describe, it, expect } from "@jest/globals"

import { barXLabels } from "components/Workspace/Visualize/Plot/barLabels"

describe("barXLabels", () => {
  it("labels bars with the saved columns, as hierarchical_clustering's 1..k", () => {
    expect(barXLabels([43, 12, 51], [1, 2, 3])).toEqual(["1", "2", "3"])
  })

  it("keeps the default 0..n-1 columns of other bar outputs", () => {
    expect(barXLabels([0.5, 0.3], [0, 1])).toEqual(["0", "1"])
  })

  it("falls back to the row keys when the columns do not match the row", () => {
    expect(barXLabels([0.5, 0.3], [])).toEqual(["0", "1"])
    expect(barXLabels([0.5, 0.3], undefined)).toEqual(["0", "1"])
  })
})
