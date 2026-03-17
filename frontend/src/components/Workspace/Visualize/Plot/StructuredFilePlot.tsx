import {
  ChangeEvent,
  memo,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react"
import PlotlyChart from "react-plotlyjs-ts"
import { useDispatch, useSelector } from "react-redux"

import { Box, LinearProgress, Typography } from "@mui/material"

import { DisplayDataContext } from "components/Workspace/Visualize/DataContext"
import { MoviePlayerControls } from "components/Workspace/Visualize/Plot/MoviePlayerControls"
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

  return (
    <Box>
      <DataView result={result} />
    </Box>
  )
})

const DataView = memo(function DataView({
  result,
}: {
  result: {
    data_type: string
    data: number[] | number[][] | number[][][]
    total_frames?: number
  }
}) {
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
  const [activeIndex, setActiveIndex] = useState(0)
  const [duration, setDuration] = useState(500)
  const intervalRef = useRef<null | NodeJS.Timeout>(null)
  const maxIndex = data.length - 1

  const frame = data[activeIndex] || []

  useEffect(() => {
    if (intervalRef.current !== null && activeIndex >= maxIndex) {
      clearInterval(intervalRef.current)
      intervalRef.current = null
    }
  }, [activeIndex, maxIndex])

  const onPlayClick = useCallback(() => {
    if (activeIndex >= maxIndex) {
      setActiveIndex(0)
    }
    if (maxIndex > 0 && intervalRef.current === null) {
      intervalRef.current = setInterval(() => {
        setActiveIndex((prev) => Math.min(prev + 1, maxIndex))
      }, duration)
    }
  }, [activeIndex, maxIndex, duration])

  const onPauseClick = () => {
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current)
      intervalRef.current = null
    }
  }

  const onDurationChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const newValue =
        event.target?.value === "" ? "" : Number(event.target?.value)
      if (typeof newValue === "number") {
        setDuration(newValue)
      }
    },
    [],
  )

  const onSliderChange = (_: Event, value: number | number[]) => {
    if (typeof value === "number") {
      setActiveIndex(value)
    }
  }

  const plotData = [
    {
      z: frame,
      type: "heatmap",
      colorscale: "Greys",
      reversescale: true,
    },
  ]

  const layout = {
    title: `Frame ${activeIndex + 1} / ${data.length} (of ${totalFrames} total)`,
    yaxis: { autorange: "reversed" as const },
    autosize: true,
  }

  return (
    <Box>
      {data.length > 1 && (
        <MoviePlayerControls
          sx={{ mt: 1 }}
          activeIndex={activeIndex}
          maxIndex={maxIndex}
          duration={duration}
          onPlayClick={onPlayClick}
          onPauseClick={onPauseClick}
          onDurationChange={onDurationChange}
          onSliderChange={onSliderChange}
        />
      )}
      <PlotlyChart data={plotData} layout={layout} />
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
