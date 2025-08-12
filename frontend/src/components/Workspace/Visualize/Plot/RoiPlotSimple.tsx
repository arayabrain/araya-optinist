import { memo, useEffect, useMemo, useState, useRef } from "react"
import PlotlyChart from "react-plotlyjs-ts"
import { useSelector, useDispatch } from "react-redux"

import createColormap from "colormap"
import { max, uniq } from "lodash"

import { LinearProgress, Typography, Box } from "@mui/material"

import { getRoiData } from "store/slice/DisplayData/DisplayDataActions"
import {
  selectRoiData,
  selectRoiDataIsPending,
  selectRoiDataError,
} from "store/slice/DisplayData/DisplayDataSelectors"
import { AppDispatch } from "store/store"

interface RoiPlotSimpleProps {
  filePath: string
  workspaceId: number
  onClick?: () => void
}

export const RoiPlotSimple = memo(function RoiPlotSimple({
  filePath,
  workspaceId,
  onClick,
}: RoiPlotSimpleProps) {
  // Use selectors instead of direct state access
  const roiDataFromSelector = useSelector(selectRoiData(filePath))
  const isPending = useSelector(selectRoiDataIsPending(filePath))
  const error = useSelector(selectRoiDataError(filePath))
  const [isMounted, setIsMounted] = useState(false)
  const plotRef = useRef<HTMLDivElement>(null)

  const roiData = useMemo(
    () => (roiDataFromSelector ? [roiDataFromSelector] : []),
    [roiDataFromSelector],
  )

  const dispatch = useDispatch<AppDispatch>()

  const maxIndex = useMemo(() => {
    if (!roiData || roiData.length === 0) return 0

    // ROI data structure: data[0] is the 2D ROI array
    const roi2DArray = roiData[0]
    if (!roi2DArray || !Array.isArray(roi2DArray)) return 0

    const flatValues = roi2DArray.map((row) => row.filter(Boolean)).flat()
    const uniqueValues = uniq(flatValues)
    const maxValue = max(uniqueValues)

    return typeof maxValue === "number" ? maxValue : 0
  }, [roiData])

  useEffect(() => {
    setIsMounted(true)
    return () => {
      setIsMounted(false)
    }
  }, [])

  useEffect(() => {
    if (workspaceId && filePath) {
      dispatch(getRoiData({ path: filePath, workspaceId }))
    }
  }, [dispatch, filePath, workspaceId])

  if (!filePath) {
    return (
      <Box
        sx={{
          width: 100,
          height: 80,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Typography variant="caption">No data</Typography>
      </Box>
    )
  }

  if (isPending) {
    return <LinearProgress />
  } else if (error != null) {
    return (
      <Typography color="error" variant="caption">
        {error}
      </Typography>
    )
  } else if (roiData && roiData.length > 0 && maxIndex > 0) {
    const roi2DArray = roiData[0] // Get the actual 2D ROI array
    const nshades = maxIndex < 100 ? Math.max(maxIndex, 6) : 100
    const colorscaleRoi = createColormap({
      colormap: "jet",
      nshades,
      format: "hex",
      alpha: 1,
    })

    const data = [
      {
        z: roi2DArray,
        type: "heatmap",
        colorscale: colorscaleRoi.map((value, idx) => [
          String(idx / (nshades - 1)),
          value,
        ]),
        showscale: false,
        hoverongaps: false,
        hoverinfo: "none",
        zmin: 0,
        zmax: maxIndex,
      },
    ]

    const layout = {
      width: 100,
      height: 80,
      margin: { t: 0, r: 0, b: 0, l: 0 },
      xaxis: { visible: false },
      yaxis: { visible: false, autorange: "reversed" },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
    }

    const config = {
      displayModeBar: false,
      responsive: true,
      staticPlot: true,
    }

    if (!isMounted) {
      return (
        <div
          ref={plotRef}
          onClick={onClick}
          style={{ cursor: "pointer", width: "100%", height: "100%" }}
        />
      )
    }

    return (
      <div
        ref={plotRef}
        onClick={onClick}
        style={{ cursor: "pointer", width: "100%", height: "100%" }}
      >
        <PlotlyChart data={data} layout={layout} config={config} />
      </div>
    )
  } else {
    return (
      <Box
        sx={{
          width: 100,
          height: 80,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Typography variant="caption">
          {isPending
            ? "Loading..."
            : `No ROI data (${roiData.length} items, max: ${maxIndex})`}
        </Typography>
      </Box>
    )
  }
})
