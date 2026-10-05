import { memo, useContext, useEffect, useMemo } from "react"
import PlotlyChart from "react-plotlyjs-ts"
import { useSelector, useDispatch } from "react-redux"

import { LinearProgress, Typography } from "@mui/material"

import { DisplayDataContext } from "components/Workspace/Visualize/DataContext"
import { filterHeatMapRows } from "components/Workspace/Visualize/Plot/heatMapRows"
import { useVisualize } from "components/Workspace/Visualize/VisualizeContext"
import { getHeatMapData } from "store/slice/DisplayData/DisplayDataActions"
import {
  selectHeatMapColumns,
  selectHeatMapData,
  selectHeatMapDataError,
  selectHeatMapDataIsFulfilled,
  selectHeatMapDataIsInitialized,
  selectHeatMapDataIsPending,
  selectHeatMapIndex,
  selectHeatMapMeta,
} from "store/slice/DisplayData/DisplayDataSelectors"
import {
  selectHeatMapItemColors,
  selectHeatMapItemRefItemId,
  selectHeatMapLinkedDrawOrderList,
  selectHeatMapItemShowScale,
  selectVisualizeItemHeight,
  selectVisualizeItemWidth,
  selectVisualizeSaveFilename,
  selectVisualizeSaveFormat,
} from "store/slice/VisualizeItem/VisualizeItemSelectors"
import { AppDispatch } from "store/store"
import { twoDimarrayEqualityFn } from "utils/EqualityUtils"

export const HeatMapPlot = memo(function HeatMapPlot() {
  const { filePath: path } = useContext(DisplayDataContext)
  const dispatch = useDispatch<AppDispatch>()
  const isPending = useSelector(selectHeatMapDataIsPending(path))
  const isInitialized = useSelector(selectHeatMapDataIsInitialized(path))
  const error = useSelector(selectHeatMapDataError(path))
  const isFulfilled = useSelector(selectHeatMapDataIsFulfilled(path))
  useEffect(() => {
    if (!isInitialized) {
      dispatch(getHeatMapData({ path }))
    }
  }, [dispatch, isInitialized, path])
  if (isPending) {
    return <LinearProgress />
  } else if (error != null) {
    return <Typography color="error">{error}</Typography>
  } else if (isFulfilled) {
    return <HeatMapImple />
  } else {
    return null
  }
})

const HeatMapImple = memo(function HeatMapImple() {
  const { filePath: path, itemId } = useContext(DisplayDataContext)
  const heatMapData = useSelector(selectHeatMapData(path), heatMapDataEqualtyFn)
  const meta = useSelector(selectHeatMapMeta(path))
  const columns = useSelector(selectHeatMapColumns(path))
  const index = useSelector(selectHeatMapIndex(path))
  const showscale = useSelector(selectHeatMapItemShowScale(itemId))
  const colorscale = useSelector(selectHeatMapItemColors(itemId))
  const width = useSelector(selectVisualizeItemWidth(itemId))
  const height = useSelector(selectVisualizeItemHeight(itemId))
  const refItemId = useSelector(selectHeatMapItemRefItemId(itemId))
  const linkedDrawOrderList = useSelector(
    selectHeatMapLinkedDrawOrderList(itemId),
  )
  const { roisClick } = useVisualize()
  const selectedRois = useMemo(
    () =>
      linkedDrawOrderList?.map(Number) ??
      (refItemId != null ? roisClick[refItemId] : undefined),
    [linkedDrawOrderList, refItemId, roisClick],
  )
  const rows = useMemo(
    () => filterHeatMapRows(heatMapData, index, selectedRois),
    [heatMapData, index, selectedRois],
  )

  const data = useMemo(
    () =>
      heatMapData != null
        ? [
            {
              z: rows.z,
              x: columns,
              y: rows.index,
              type: "heatmap",
              name: "heatmap",
              colorscale: colorscale.map((value) => {
                let offset: number = parseFloat(value.offset)
                const offsets: number[] = colorscale.map((v) => {
                  return parseFloat(v.offset)
                })
                // plotly requires the end [0.0, 1.0] to be set
                if (offset === Math.max(...offsets)) {
                  offset = 1.0
                }
                if (offset === Math.min(...offsets)) {
                  offset = 0.0
                }
                return [offset, value.rgb]
              }),
              hoverongaps: false,
              showlegend: true,
              showscale: showscale,
              colorbar: {
                len: 0.95,
                y: 0.45,
                yanchor: "middle",
                thickness: 20,
              },
            },
          ]
        : [],
    [heatMapData, rows, showscale, colorscale, columns],
  )

  const layout = useMemo(
    () => ({
      title: {
        text: meta?.title,
        x: 0.1,
      },
      width: width,
      height: height - 50,
      dragmode: "pan",
      margin: {
        t: 50, // top
        l: 50, // left
        b: 40, // bottom
      },
      autosize: true,
      xaxis: {
        title: meta?.xlabel,
        type: meta?.xaxis_type,
      },
      yaxis: {
        title: meta?.ylabel,
        // A filtered subset of rows is discrete even if the full index is not.
        type: selectedRois?.length ? "category" : meta?.yaxis_type,
      },
    }),
    [meta, width, height, selectedRois],
  )

  const saveFileName = useSelector(selectVisualizeSaveFilename(itemId))
  const saveFormat = useSelector(selectVisualizeSaveFormat(itemId))

  const config = {
    displayModeBar: true,
    responsive: true,
    toImageButtonOptions: {
      format: saveFormat,
      filename: saveFileName,
    },
  }

  return <PlotlyChart data={data} layout={layout} config={config} />
})

function heatMapDataEqualtyFn(
  a: number[][] | undefined,
  b: number[][] | undefined,
) {
  if (a != null && b != null) {
    return twoDimarrayEqualityFn(a, b)
  } else {
    return a === undefined && b === undefined
  }
}
