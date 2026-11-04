import { ChangeEvent, FC, useContext } from "react"
import { useSelector, useDispatch } from "react-redux"

import { Grid, MenuItem, SelectChangeEvent } from "@mui/material"

import { FieldLabel, ParamSection } from "components/common/ParamSection"
import { ParamSelect } from "components/common/ParamSelect"
import { ParamSwitch } from "components/common/ParamSwitch"
import { ParamTextField } from "components/common/ParamTextField"
import { GradientColorPicker } from "components/Workspace/Visualize/Editor/GradientColorPicker"
import { SaveFig } from "components/Workspace/Visualize/Editor/SaveFig"
import { SelectedItemIdContext } from "components/Workspace/Visualize/VisualizeItemEditor"
import {
  selectHeatMapItemColors,
  selectHeatMapItemShowScale,
  selectHeatMapItemZRange,
  selectHeatMapItemZRangeMode,
  selectHeatMapItemZRangePercentile,
} from "store/slice/VisualizeItem/VisualizeItemSelectors"
import {
  setHeatMapItemColors,
  setHeatMapItemShowScale,
  setHeatMapItemZRange,
  setHeatMapItemZRangeMode,
  setHeatMapItemZRangePercentile,
} from "store/slice/VisualizeItem/VisualizeItemSlice"
import {
  ColorType,
  HeatMapZRangeMode,
} from "store/slice/VisualizeItem/VisualizeItemType"

export const HeatmapItemEditor: FC = () => {
  const itemId = useContext(SelectedItemIdContext)
  const dispatch = useDispatch()
  const colors = useSelector(selectHeatMapItemColors(itemId))
  const dispathSetColor = (colorCode: ColorType[]) => {
    dispatch(setHeatMapItemColors({ itemId, colors: colorCode }))
  }
  return (
    <>
      <ParamSection title="Heatmap">
        <ShowScale />
        <ColorRange />
        <Grid container component="label" alignItems="center">
          <Grid item xs={8}>
            <FieldLabel>Pick Color</FieldLabel>
          </Grid>
          <Grid item xs={4}>
            <GradientColorPicker
              colors={colors}
              dispatchSetColor={dispathSetColor}
            />
          </Grid>
        </Grid>
      </ParamSection>
      <SaveFig />
    </>
  )
}

const ShowScale: FC = () => {
  const itemId = useContext(SelectedItemIdContext)
  const showscale = useSelector(selectHeatMapItemShowScale(itemId))
  const dispatch = useDispatch()
  const toggleChecked = () => {
    dispatch(setHeatMapItemShowScale({ itemId, showscale: !showscale }))
  }
  return (
    <ParamSwitch label="ShowScale" value={showscale} onChange={toggleChecked} />
  )
}

const ColorRange: FC = () => {
  const itemId = useContext(SelectedItemIdContext)
  const mode = useSelector(selectHeatMapItemZRangeMode(itemId))
  const percentile = useSelector(selectHeatMapItemZRangePercentile(itemId))
  const zRange = useSelector(selectHeatMapItemZRange(itemId))
  const dispatch = useDispatch()
  const onChangeMode = (event: SelectChangeEvent<string>) => {
    dispatch(
      setHeatMapItemZRangeMode({
        itemId,
        zRangeMode: event.target.value as HeatMapZRangeMode,
      }),
    )
  }
  const onChangePercentile = (event: ChangeEvent<HTMLInputElement>) => {
    const value = Number(event.target.value)
    if (event.target.value !== "" && value >= 0 && value < 50) {
      dispatch(
        setHeatMapItemZRangePercentile({ itemId, zRangePercentile: value }),
      )
    }
  }
  const onChangeBound =
    (bound: 0 | 1) => (event: ChangeEvent<HTMLInputElement>) => {
      const value = Number(event.target.value)
      if (event.target.value === "" || !Number.isFinite(value)) return
      const next: [number, number] = [zRange?.[0] ?? 0, zRange?.[1] ?? 1]
      next[bound] = value
      dispatch(setHeatMapItemZRange({ itemId, zRange: next }))
    }
  return (
    <>
      <ParamSelect label="Color Range" value={mode} onChange={onChangeMode}>
        <MenuItem value="auto">Data min to max</MenuItem>
        <MenuItem value="percentile">Percentile clip</MenuItem>
        <MenuItem value="manual">Manual</MenuItem>
      </ParamSelect>
      {mode === "percentile" && (
        <ParamTextField
          type="number"
          label="Clip percent (each end)"
          inputProps={{ min: 0, max: 49, step: 0.5 }}
          value={percentile}
          onChange={onChangePercentile}
        />
      )}
      {mode === "manual" && (
        <Grid container spacing={1}>
          <Grid item xs={6}>
            <ParamTextField
              type="number"
              label="Min"
              value={zRange?.[0] ?? ""}
              onChange={onChangeBound(0)}
            />
          </Grid>
          <Grid item xs={6}>
            <ParamTextField
              type="number"
              label="Max"
              value={zRange?.[1] ?? ""}
              onChange={onChangeBound(1)}
            />
          </Grid>
        </Grid>
      )}
    </>
  )
}
