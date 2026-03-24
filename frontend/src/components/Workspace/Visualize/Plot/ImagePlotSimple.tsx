import { memo, useEffect, useState } from "react"
import PlotlyChart from "react-plotlyjs-ts"
import { useSelector, useDispatch } from "react-redux"

import CloudDownloadIcon from "@mui/icons-material/CloudDownload"
import CloudSyncIcon from "@mui/icons-material/CloudSync"
import {
  CircularProgress,
  LinearProgress,
  Typography,
  Box,
  IconButton,
  Tooltip,
} from "@mui/material"

import {
  getImageData,
  SYNC_IN_PROGRESS_MESSAGE,
} from "store/slice/DisplayData/DisplayDataActions"
import {
  selectImageData,
  selectImageDataIsPending,
  selectImageDataIsInitialized,
  selectImageDataError,
  selectImageDataErrorStatus,
} from "store/slice/DisplayData/DisplayDataSelectors"
import { AppDispatch } from "store/store"

interface ImagePlotSimpleProps {
  filePath: string
  workspaceId: number
  uniqueId?: string
  onClick?: () => void
}

export const ImagePlotSimple = memo(function ImagePlotSimple({
  filePath,
  workspaceId,
  uniqueId,
  onClick,
}: ImagePlotSimpleProps) {
  // Use selectors instead of direct state access
  const imageState = useSelector(selectImageData(filePath))
  const isPending = useSelector(selectImageDataIsPending(filePath))
  const isInitialized = useSelector(selectImageDataIsInitialized(filePath))
  const error = useSelector(selectImageDataError(filePath))
  const errorStatus = useSelector(selectImageDataErrorStatus(filePath))
  const imageData = imageState?.data?.[0]

  const dispatch = useDispatch<AppDispatch>()

  useEffect(() => {
    if (workspaceId && !isInitialized && filePath) {
      dispatch(
        getImageData({
          path: filePath,
          workspaceId,
          uniqueId,
          startIndex: 1,
          endIndex: 1,
        }),
      )
    }
  }, [dispatch, isInitialized, filePath, workspaceId, uniqueId])

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

  const handleRetry = () => {
    if (workspaceId && filePath) {
      dispatch(
        getImageData({
          path: filePath,
          workspaceId,
          uniqueId,
          startIndex: 1,
          endIndex: 1,
        }),
      )
    }
  }

  if (isPending) {
    return <LinearProgress />
  } else if (error != null) {
    const isSyncing = error === SYNC_IN_PROGRESS_MESSAGE
    const isNotFound = errorStatus === 404
    return (
      <Box
        sx={{
          width: 100,
          height: 80,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 0.5,
        }}
      >
        <Typography
          color={isSyncing ? "text.secondary" : "error"}
          variant="caption"
          sx={{ fontSize: "0.65rem" }}
        >
          {error}
        </Typography>
        {!isNotFound && (
          <Tooltip title={isSyncing ? "Retry sync" : "Download"}>
            <IconButton
              size="small"
              onClick={(e) => {
                e.stopPropagation()
                handleRetry()
              }}
              sx={{ padding: 0.25 }}
            >
              <CloudDownloadIcon sx={{ fontSize: 16 }} color="primary" />
            </IconButton>
          </Tooltip>
        )}
      </Box>
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

interface ImagePlotSimpleWithLoadingProps extends ImagePlotSimpleProps {
  /** If true, show a sync indicator for legacy TIFF files that may need on-demand sync */
  showSyncIndicator?: boolean
}

/**
 * ImagePlotSimple with loading state indicator for legacy TIFF files.
 *
 * Legacy TIFFs may need on-demand sync from S3, which can take longer.
 * This component shows a sync indicator when loading potentially large files.
 *
 * Note: PNG thumbnails are handled directly by the parent component (DataviewRecords)
 * and don't go through this component.
 */
export const ImagePlotSimpleWithLoading = memo(
  function ImagePlotSimpleWithLoading({
    filePath,
    workspaceId,
    uniqueId,
    onClick,
    showSyncIndicator = true,
  }: ImagePlotSimpleWithLoadingProps) {
    const [isSyncing, setIsSyncing] = useState(false)
    const isPending = useSelector(selectImageDataIsPending(filePath ?? ""))
    const isInitialized = useSelector(
      selectImageDataIsInitialized(filePath ?? ""),
    )

    // Show sync indicator when loading
    useEffect(() => {
      if (showSyncIndicator && isPending && !isInitialized) {
        setIsSyncing(true)
      } else if (isInitialized) {
        setIsSyncing(false)
      }
    }, [showSyncIndicator, isPending, isInitialized])

    return (
      <Box sx={{ position: "relative", width: 100, height: 80 }}>
        <ImagePlotSimple
          filePath={filePath}
          workspaceId={workspaceId}
          uniqueId={uniqueId}
          onClick={onClick}
        />
        {isSyncing && (
          <Box
            sx={{
              position: "absolute",
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              backgroundColor: "rgba(255, 255, 255, 0.8)",
              gap: 0.5,
            }}
          >
            <CircularProgress size={20} />
            <Tooltip title="Syncing from cloud storage">
              <CloudSyncIcon sx={{ fontSize: 14, color: "primary.main" }} />
            </Tooltip>
          </Box>
        )}
      </Box>
    )
  },
)
