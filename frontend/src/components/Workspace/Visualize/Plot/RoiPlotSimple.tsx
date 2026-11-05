import { memo, useEffect, useMemo, useState, useRef } from "react"
import PlotlyChart from "react-plotlyjs-ts"
import { useSelector, useDispatch } from "react-redux"

import createColormap from "colormap"
import { max, uniq } from "lodash"

import CloudDownloadIcon from "@mui/icons-material/CloudDownload"
import CloudSyncIcon from "@mui/icons-material/CloudSync"
import {
  CircularProgress,
  Typography,
  Box,
  IconButton,
  Tooltip,
} from "@mui/material"

import {
  getRoiData,
  SYNC_IN_PROGRESS_MESSAGE,
} from "store/slice/DisplayData/DisplayDataActions"
import {
  selectRoiData,
  selectRoiDataIsPending,
  selectRoiDataIsInitialized,
  selectRoiDataError,
  selectRoiDataErrorStatus,
} from "store/slice/DisplayData/DisplayDataSelectors"
import { AppDispatch } from "store/store"

interface RoiPlotSimpleProps {
  filePath: string
  workspaceId: number
  uniqueId?: string
  onClick?: () => void
}

export const RoiPlotSimple = memo(function RoiPlotSimple({
  filePath,
  workspaceId,
  uniqueId,
  onClick,
}: RoiPlotSimpleProps) {
  // Use selectors instead of direct state access
  const roiDataFromSelector = useSelector(selectRoiData(filePath))
  const isPending = useSelector(selectRoiDataIsPending(filePath))
  const isInitialized = useSelector(selectRoiDataIsInitialized(filePath))
  const error = useSelector(selectRoiDataError(filePath))
  const errorStatus = useSelector(selectRoiDataErrorStatus(filePath))
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

  const wasInitialized = useRef(isInitialized)

  useEffect(() => {
    // A wiped entry refetches; the rerun from this fetch's own pending entry does not.
    const ownFetchRegistered = isInitialized && !wasInitialized.current
    wasInitialized.current = isInitialized
    if (workspaceId && filePath && !ownFetchRegistered) {
      dispatch(getRoiData({ path: filePath, workspaceId, uniqueId }))
    }
  }, [dispatch, filePath, workspaceId, uniqueId, isInitialized])

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
      dispatch(getRoiData({ path: filePath, workspaceId, uniqueId }))
    }
  }

  // With a workspaceId, not-yet-initialized means the effect above has a fetch coming.
  if (isPending || (!isInitialized && !!workspaceId)) {
    return (
      <Box
        sx={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 0.5,
        }}
      >
        <CircularProgress size={20} aria-label="Loading thumbnail" />
        <Tooltip title="May need to sync from cloud storage">
          <CloudSyncIcon sx={{ fontSize: 14, color: "primary.main" }} />
        </Tooltip>
      </Box>
    )
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
        <Typography variant="caption">No ROI data</Typography>
      </Box>
    )
  }
})
