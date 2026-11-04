import { ChangeEvent, FC, useContext, useState } from "react"
import { useSelector, useDispatch } from "react-redux"

import {
  Grid,
  MenuItem,
  SelectChangeEvent,
  TextFieldProps,
} from "@mui/material"

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
  const setBound = (bound: 0 | 1) => (value: number) => {
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
        <DraftNumberField
          label="Clip percent (each end)"
          inputProps={{ min: 0, max: 49, step: 0.5 }}
          value={percentile}
          accepts={(value) => value >= 0 && value < 50}
          onCommit={(value) =>
            dispatch(
              setHeatMapItemZRangePercentile({
                itemId,
                zRangePercentile: value,
              }),
            )
          }
        />
      )}
      {mode === "manual" && (
        <Grid container spacing={1}>
          <Grid item xs={6}>
            <DraftNumberField
              label="Min"
              value={zRange?.[0] ?? null}
              onCommit={setBound(0)}
            />
          </Grid>
          <Grid item xs={6}>
            <DraftNumberField
              label="Max"
              value={zRange?.[1] ?? null}
              onCommit={setBound(1)}
            />
          </Grid>
        </Grid>
      )}
    </>
  )
}

// The field shows what was typed ("-", "", "1.50") and commits each finite value;
// a value changed from outside (another item selected) shows through.
export const DraftNumberField: FC<{
  label: string
  value: number | null
  onCommit: (value: number) => void
  accepts?: (value: number) => boolean
  inputProps?: TextFieldProps["inputProps"]
}> = ({ label, value, onCommit, accepts = () => true, inputProps }) => {
  const [draft, setDraft] = useState({ text: asText(value), of: value })
  const text = draft.of === value ? draft.text : asText(value)
  const onChange = (event: ChangeEvent<HTMLInputElement>) => {
    const next = Number(event.target.value)
    const ok =
      event.target.value !== "" && Number.isFinite(next) && accepts(next)
    setDraft({ text: event.target.value, of: ok ? next : value })
    if (ok) onCommit(next)
  }
  return (
    <ParamTextField
      type="number"
      label={label}
      value={text}
      onChange={onChange}
      inputProps={inputProps}
    />
  )
}

const asText = (value: number | null) => (value == null ? "" : String(value))
