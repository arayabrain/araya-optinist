import { memo, useEffect } from "react"
import PlotlyChart from "react-plotlyjs-ts"
import { useSelector, useDispatch } from "react-redux"

import { LinearProgress, Typography, Box } from "@mui/material"

import { getImageData } from "store/slice/DisplayData/DisplayDataActions"
import { AppDispatch, RootState } from "store/store"

interface ImagePlotSimpleProps {
  filePath: string
  workspaceId: number
  onClick?: () => void
}

export const ImagePlotSimple = memo(function ImagePlotSimple({
  filePath,
  workspaceId,
  onClick,
}: ImagePlotSimpleProps) {
  // Safe selectors with fallbacks
  const imageState = useSelector(
    (state: RootState) => state.displayData?.image?.[filePath],
  )
  const isPending = imageState?.pending || false
  const isInitialized = imageState !== undefined
  const error = imageState?.error || null
  const imageData = imageState?.data?.[0]

  const dispatch = useDispatch<AppDispatch>()

  useEffect(() => {
    if (workspaceId && !isInitialized && filePath) {
      dispatch(
        getImageData({
          path: filePath,
          workspaceId,
          startIndex: 1,
          endIndex: 1,
        }),
      )
    }
  }, [dispatch, isInitialized, filePath, workspaceId])

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
  } else if (imageData && Array.isArray(imageData) && imageData.length > 0) {
    // Ensure imageData is 2D array for heatmap
    const zData = Array.isArray(imageData[0]) ? imageData : [imageData]

    const data = [
      {
        z: zData,
        type: "heatmap",
        colorscale: "Viridis",
        showscale: false,
        hoverongaps: false,
        hoverinfo: "none",
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

    return (
      <div
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
            : `No image data (${imageData ? "has data" : "no data"})`}
        </Typography>
      </Box>
    )
  }
})
