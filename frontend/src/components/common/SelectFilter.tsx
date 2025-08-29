import { Fragment } from "react"

import { Autocomplete, Chip, styled, TextField, Tooltip } from "@mui/material"
import { GridFilterItem } from "@mui/x-data-grid"

import { DELAY_TIME_INPUT_CONFIRMED } from "const/Form"

type SelectFilterProps = {
  loading: boolean
  applyValue: (value: GridFilterItem) => void
  item: GridFilterItem
  timeout: NodeJS.Timeout | undefined
  options: string[]
}

const SelectFilter = ({
  loading,
  applyValue,
  item,
  timeout,
  options,
}: SelectFilterProps) => {
  if (!options) return null
  return (
    <AutocompleteCustom
      autoFocus={!loading}
      sx={{ paddingTop: "16px" }}
      defaultValue={item.value || []}
      onChange={(e, newValue) => {
        if (timeout) clearTimeout(timeout)
        timeout = setTimeout(() => {
          applyValue({ ...item, value: newValue })
        }, DELAY_TIME_INPUT_CONFIRMED)
      }}
      multiple={true}
      disableCloseOnSelect
      options={options
        .map((item) => ({ value: item, title: item }))
        .map((option) => option.title.toString())}
      renderInput={(params) => (
        <TextField placeholder={"Filter value"} {...params} />
      )}
      renderTags={(tagValue, getTagProps) => {
        return tagValue.map((option, index) => (
          <Fragment key={index}>
            <Tooltip title={tagValue[index] as string} placement={"right"}>
              <Chip
                {...getTagProps({ index })}
                label={tagValue[index] as string}
              />
            </Tooltip>
          </Fragment>
        ))
      }}
    />
  )
}

const AutocompleteCustom = styled(Autocomplete)(() => ({
  ".MuiAutocomplete-tag": {
    height: 25,
  },
}))

export default SelectFilter
