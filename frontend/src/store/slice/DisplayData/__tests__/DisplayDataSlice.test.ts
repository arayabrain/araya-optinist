import { describe, it, expect } from "@jest/globals"

import {
  getRoiData,
  getImageData,
} from "store/slice/DisplayData/DisplayDataActions"
import reducer from "store/slice/DisplayData/DisplayDataSlice"

describe("DisplayDataSlice", () => {
  const initialState = {
    timeSeries: {},
    heatMap: {},
    image: {},
    csv: {},
    roi: {},
    scatter: {},
    bar: {},
    html: {},
    histogram: {},
    line: {},
    pie: {},
    polar: {},
    loading: false,
    loadingStack: [],
    statusRoi: {
      temp_add_roi: [],
      temp_delete_roi: [],
      temp_merge_roi: [],
    },
    isEditRoiCommitting: false,
  }

  describe("getRoiData.rejected", () => {
    it("shows 'Data not synced' for all rejected requests", () => {
      const pendingState = {
        ...initialState,
        loading: true,
        loadingStack: [true],
        roi: {
          "/test/path": {
            type: "roi" as const,
            data: [],
            pending: true,
            fulfilled: false,
            error: null,
            roiUniqueList: [],
          },
        },
      }

      const action = {
        type: getRoiData.rejected.type,
        meta: { arg: { path: "/test/path", workspaceId: 1 } },
        payload: {
          response: { status: 500, data: { message: "Internal server error" } },
          message: "Request failed with status code 500",
        },
        error: { message: "Rejected" },
      }

      const state = reducer(pendingState, action)

      expect(state.roi["/test/path"].error).toBe("Data not synced")
      expect(state.roi["/test/path"].pending).toBe(false)
      expect(state.roi["/test/path"].fulfilled).toBe(false)
    })

    it("updates loading state correctly", () => {
      const pendingState = {
        ...initialState,
        loading: true,
        loadingStack: [true],
        roi: {
          "/test/path": {
            type: "roi" as const,
            data: [],
            pending: true,
            fulfilled: false,
            error: null,
            roiUniqueList: [],
          },
        },
      }

      const action = {
        type: getRoiData.rejected.type,
        meta: { arg: { path: "/test/path", workspaceId: 1 } },
        payload: { message: "Error" },
        error: { message: "Rejected" },
      }

      const state = reducer(pendingState, action)

      expect(state.loading).toBe(false)
      expect(state.loadingStack).toHaveLength(0)
    })
  })

  describe("getImageData.rejected", () => {
    it("shows 'Image not synced' for all rejected requests", () => {
      const pendingState = {
        ...initialState,
        image: {
          "/test/image.tiff": {
            type: "image" as const,
            data: [],
            pending: true,
            fulfilled: false,
            error: null,
          },
        },
      }

      const action = {
        type: getImageData.rejected.type,
        meta: {
          arg: {
            path: "/test/image.tiff",
            workspaceId: 1,
            startIndex: 1,
            endIndex: 1,
          },
        },
        payload: {
          response: { status: 500, data: { message: "Internal server error" } },
          message: "Request failed with status code 500",
        },
        error: { message: "Rejected" },
      }

      const state = reducer(pendingState, action)

      expect(state.image["/test/image.tiff"].error).toBe("Image not synced")
      expect(state.image["/test/image.tiff"].pending).toBe(false)
      expect(state.image["/test/image.tiff"].fulfilled).toBe(false)
    })
  })

  describe("getRoiData.pending", () => {
    it("sets pending state and adds to loading stack", () => {
      const action = {
        type: getRoiData.pending.type,
        meta: { arg: { path: "/test/path", workspaceId: 1 } },
      }

      const state = reducer(initialState, action)

      expect(state.roi["/test/path"].pending).toBe(true)
      expect(state.roi["/test/path"].fulfilled).toBe(false)
      expect(state.roi["/test/path"].error).toBe(null)
      expect(state.loading).toBe(true)
      expect(state.loadingStack).toHaveLength(1)
    })
  })

  describe("getImageData.pending", () => {
    it("sets pending state for image", () => {
      const action = {
        type: getImageData.pending.type,
        meta: {
          arg: {
            path: "/test/image.tiff",
            workspaceId: 1,
            startIndex: 1,
            endIndex: 1,
          },
        },
      }

      const state = reducer(initialState, action)

      expect(state.image["/test/image.tiff"].pending).toBe(true)
      expect(state.image["/test/image.tiff"].fulfilled).toBe(false)
      expect(state.image["/test/image.tiff"].error).toBe(null)
    })
  })

  describe("getRoiData.fulfilled", () => {
    it("sets fulfilled state with data", () => {
      const pendingState = {
        ...initialState,
        loading: true,
        loadingStack: [true],
        roi: {
          "/test/path": {
            type: "roi" as const,
            data: [],
            pending: true,
            fulfilled: false,
            error: null,
            roiUniqueList: [],
          },
        },
      }

      const action = {
        type: getRoiData.fulfilled.type,
        meta: { arg: { path: "/test/path", workspaceId: 1 } },
        payload: {
          data: [
            [
              [1, 2, 3],
              [4, 5, 6],
            ],
          ],
          meta: { title: "ROI Data" },
        },
      }

      const state = reducer(pendingState, action)

      expect(state.roi["/test/path"].pending).toBe(false)
      expect(state.roi["/test/path"].fulfilled).toBe(true)
      expect(state.roi["/test/path"].error).toBe(null)
      expect(state.roi["/test/path"].data).toHaveLength(1)
      expect(state.loading).toBe(false)
    })
  })

  describe("getImageData.fulfilled", () => {
    it("sets fulfilled state with data", () => {
      const pendingState = {
        ...initialState,
        image: {
          "/test/image.tiff": {
            type: "image" as const,
            data: [],
            pending: true,
            fulfilled: false,
            error: null,
          },
        },
      }

      const action = {
        type: getImageData.fulfilled.type,
        meta: {
          arg: {
            path: "/test/image.tiff",
            workspaceId: 1,
            startIndex: 1,
            endIndex: 1,
          },
        },
        payload: {
          data: [
            [
              [1, 2, 3],
              [4, 5, 6],
            ],
          ],
          meta: { title: "Image Data" },
        },
      }

      const state = reducer(pendingState, action)

      expect(state.image["/test/image.tiff"].pending).toBe(false)
      expect(state.image["/test/image.tiff"].fulfilled).toBe(true)
      expect(state.image["/test/image.tiff"].error).toBe(null)
      expect(state.image["/test/image.tiff"].data).toHaveLength(1)
    })
  })
})
