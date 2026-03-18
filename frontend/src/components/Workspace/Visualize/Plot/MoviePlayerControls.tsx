import { ChangeEvent, memo } from "react"

import { Box, Button, Slider, TextField } from "@mui/material"
import { SxProps, Theme } from "@mui/material/styles"

interface MoviePlayerControlsProps {
  activeIndex: number
  minIndex?: number
  maxIndex: number
  duration: number
  onPlayClick: () => void
  onPauseClick: () => void
  onDurationChange: (event: ChangeEvent<HTMLInputElement>) => void
  onSliderChange: (event: Event, value: number | number[]) => void
  sx?: SxProps<Theme>
}

export const MoviePlayerControls = memo(function MoviePlayerControls({
  activeIndex,
  minIndex = 0,
  maxIndex,
  duration,
  onPlayClick,
  onPauseClick,
  onDurationChange,
  onSliderChange,
  sx,
}: MoviePlayerControlsProps) {
  return (
    <Box sx={sx}>
      <Box sx={{ px: 1 }}>
        <Button sx={{ mt: 1.5 }} variant="outlined" onClick={onPlayClick}>
          Play
        </Button>
        <Button
          sx={{ mt: 1.5, ml: 1 }}
          variant="outlined"
          onClick={onPauseClick}
        >
          Pause
        </Button>
        <TextField
          sx={{ width: 100, ml: 2 }}
          label="msec/frame"
          type="number"
          inputProps={{
            step: 100,
            min: 0,
            max: 1000,
          }}
          InputLabelProps={{
            shrink: true,
          }}
          onChange={onDurationChange}
          value={duration}
        />
        <Slider
          aria-label="Custom marks"
          value={activeIndex}
          valueLabelDisplay="auto"
          step={1}
          marks
          min={minIndex}
          max={maxIndex}
          onChange={onSliderChange}
        />
      </Box>
    </Box>
  )
})
