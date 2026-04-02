import { getExperimentUidFromFilePath } from "components/Workspace/Visualize/DisplayDataItem"

describe("getExperimentUidFromFilePath", () => {
  it("extracts experiment UID from standard output path", () => {
    expect(
      getExperimentUidFromFilePath("8/a5feff3e/cca_e9mbfm8vck/coef.json"),
    ).toBe("a5feff3e")
  })

  it("extracts UID from timeseries path", () => {
    expect(
      getExperimentUidFromFilePath(
        "8/a5feff3e/suite2p_roi_yvqcdmsg0r/fluorescence",
      ),
    ).toBe("a5feff3e")
  })

  it("extracts UID from tiff subdirectory path", () => {
    expect(
      getExperimentUidFromFilePath(
        "8/a5feff3e/suite2p_registration_xq4pexs8o7/tiff/mc_images/mc_images.tif",
      ),
    ).toBe("a5feff3e")
  })

  it("returns empty string for null", () => {
    expect(getExperimentUidFromFilePath(null)).toBe("")
  })

  it("returns empty string for undefined", () => {
    expect(getExperimentUidFromFilePath(undefined)).toBe("")
  })

  it("returns empty string for empty string", () => {
    expect(getExperimentUidFromFilePath("")).toBe("")
  })

  it("returns empty string for single-segment path", () => {
    expect(getExperimentUidFromFilePath("onlyone")).toBe("")
  })
})
