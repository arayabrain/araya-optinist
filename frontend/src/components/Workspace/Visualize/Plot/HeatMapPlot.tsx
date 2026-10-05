import { memo, useContext, useEffect, useMemo } from "react"
import PlotlyChart from "react-plotlyjs-ts"
import { useSelector, useDispatch } from "react-redux"

import { LinearProgress, Typography } from "@mui/material"

import { DisplayDataContext } from "components/Workspace/Visualize/DataContext"
import {
  filterHeatMapColumns,
  filterHeatMapRows,
  heatMapZRange,
} from "components/Workspace/Visualize/Plot/heatMapRows"
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
  // Only outputs that label their rows with ROI numbers declare a categorical y axis;
  // older outputs and non-ROI heatmaps keep a positional index and must not be filtered.
  const rowsAreRois = meta?.yaxis_type === "category"
  const columnsAreRois = meta?.xaxis_type === "category"
  const selectedRois = useMemo(
    () =>
      rowsAreRois
        ? (linkedDrawOrderList?.map(Number) ??
          (refItemId != null ? roisClick[refItemId] : undefined))
        : undefined,
    [rowsAreRois, linkedDrawOrderList, refItemId, roisClick],
  )
  const rows = useMemo(
    () => filterHeatMapRows(heatMapData, index, selectedRois),
    [heatMapData, index, selectedRois],
  )
  const cols = useMemo(
    () =>
      columnsAreRois
        ? filterHeatMapColumns(rows.z, columns, selectedRois)
        : { z: rows.z, columns },
    [columnsAreRois, rows, columns, selectedRois],
  )
  // Colour scale stays that of the whole matrix while rows are filtered.
  const zRange = useMemo(() => heatMapZRange(heatMapData), [heatMapData])

  const data = useMemo(
    () =>
      heatMapData != null
        ? [
            {
              z: cols.z,
              x: cols.columns,
              y: rows.index,
              zmin: zRange?.[0],
              zmax: zRange?.[1],
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
    [heatMapData, rows, cols, zRange, showscale, colorscale],
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

  if (selectedRois?.length && rows.z.length === 0) {
    return (
      <Typography variant="body2" sx={{ p: 2 }}>
        None of the selected ROIs are in this heatmap.
      </Typography>
    )
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
