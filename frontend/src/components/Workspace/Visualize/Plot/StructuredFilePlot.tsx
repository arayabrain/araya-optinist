import { memo, useContext, useEffect, useState } from "react"
import PlotlyChart from "react-plotlyjs-ts"
import { useDispatch, useSelector } from "react-redux"

import { Box, LinearProgress, Slider, Typography } from "@mui/material"

import { DisplayDataContext } from "components/Workspace/Visualize/DataContext"
import { getStructuredData } from "store/slice/DisplayData/DisplayDataActions"
import { selectPipelineLatestUid } from "store/slice/Pipeline/PipelineSelectors"
import { selectCurrentWorkspaceId } from "store/slice/Workspace/WorkspaceSelector"
import { AppDispatch, RootState } from "store/store"

const DEFAULT_START_INDEX = 0
const DEFAULT_END_INDEX = 10

export const StructuredFilePlot = memo(function StructuredFilePlot() {
  const { nodeId, itemId } = useContext(DisplayDataContext)
  const dispatch = useDispatch<AppDispatch>()

  const workspaceId = useSelector(selectCurrentWorkspaceId)
  const uid = useSelector(selectPipelineLatestUid)

  const structuredState = useSelector(
    (state: RootState) => state.displayData.structured[itemId],
  )

  useEffect(() => {
    if (workspaceId && uid && nodeId) {
      dispatch(
        getStructuredData({
          workspaceId: String(workspaceId),
          uniqueId: uid,
          nodeId,
          itemId,
          startIndex: DEFAULT_START_INDEX,
          endIndex: DEFAULT_END_INDEX,
        }),
      )
    }
  }, [dispatch, workspaceId, uid, nodeId, itemId])

  if (!uid || !nodeId) {
    return (
      <Box p={2}>
        <Typography color="text.secondary">
          No workflow run available. Please run the workflow first.
        </Typography>
      </Box>
    )
  }

  if (!structuredState || structuredState.pending) {
    return <LinearProgress />
  }

  if (structuredState.error) {
    return (
      <Box p={2}>
        <Typography color="error">
          Error loading data: {structuredState.error}
        </Typography>
      </Box>
    )
  }

  const result = structuredState.data
  if (!result) return null

  switch (result.data_type) {
    case "timeseries":
      return <TimeSeriesView data={result.data as number[][]} />
    case "images":
      return (
        <ImageView
          data={result.data as number[][][]}
          totalFrames={
            result.total_frames ?? (result.data as number[][][]).length
          }
        />
      )
    case "bar":
      return <BarView data={result.data as number[]} />
    default:
      return <Typography>Unsupported data type: {result.data_type}</Typography>
  }
})

const TimeSeriesView = memo(function TimeSeriesView({
  data,
}: {
  data: number[][]
}) {
  const traces = []
  if (data.length > 0) {
    const numCols = data[0].length
    for (let col = 0; col < numCols; col++) {
      traces.push({
        y: data.map((row) => row[col]),
        type: "scatter",
        mode: "lines",
        name: `col ${col}`,
      })
    }
  }

  const layout = {
    title: "Time Series",
    xaxis: { title: "Frame" },
    yaxis: { title: "Value" },
    autosize: true,
  }

  return <PlotlyChart data={traces} layout={layout} />
})

const ImageView = memo(function ImageView({
  data,
  totalFrames,
}: {
  data: number[][][]
  totalFrames: number
}) {
  const [frameIndex, setFrameIndex] = useState(0)
  const frame = data[frameIndex] || []

  const plotData = [
    {
      z: frame,
      type: "heatmap",
      colorscale: "Greys",
      reversescale: true,
    },
  ]

  const layout = {
    title: `Frame ${frameIndex + 1} / ${data.length} (of ${totalFrames} total)`,
    yaxis: { autorange: "reversed" as const },
    autosize: true,
  }

  return (
    <Box>
      <PlotlyChart data={plotData} layout={layout} />
      {data.length > 1 && (
        <Box px={2}>
          <Slider
            value={frameIndex}
            min={0}
            max={data.length - 1}
            onChange={(_, value) => setFrameIndex(value as number)}
            valueLabelDisplay="auto"
            valueLabelFormat={(v) => `Frame ${v + 1}`}
          />
        </Box>
      )}
    </Box>
  )
})

const BarView = memo(function BarView({ data }: { data: number[] }) {
  const plotData = [
    {
      x: data.map((_, i) => i),
      y: data,
      type: "bar",
    },
  ]

  const layout = {
    title: "Values",
    xaxis: { title: "Index" },
    yaxis: { title: "Value" },
    autosize: true,
  }

  return <PlotlyChart data={plotData} layout={layout} />
})
