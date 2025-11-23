import { FC, ReactNode } from "react"

import MenuIcon from "@mui/icons-material/Menu"
import MenuOpenIcon from "@mui/icons-material/MenuOpen"
import { Box, IconButton, Tooltip } from "@mui/material"
import { grey } from "@mui/material/colors"

import { DRAWER_WIDTH, CONTENT_HEIGHT } from "const/Layout"

interface LeftSidebarContainerProps {
  children: ReactNode
  isOpen: boolean
  onToggle: () => void
}

export const LeftSidebarContainer: FC<LeftSidebarContainerProps> = ({
  children,
  isOpen,
  onToggle,
}) => {
  if (!isOpen) {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          paddingTop: 1,
          paddingLeft: 1,
        }}
      >
        <Tooltip title="Open sidebar" placement="right">
          <IconButton
            onClick={onToggle}
            sx={{
              backgroundColor: "transparent",
              borderRadius: "6px",
              width: 36,
              height: 36,
              color: grey[700],
              "&:hover": {
                backgroundColor: grey[100],
                color: grey[900],
              },
            }}
          >
            <MenuIcon />
          </IconButton>
        </Tooltip>
      </Box>
    )
  }

  return (
    <Box
      width={DRAWER_WIDTH}
      overflow="auto"
      marginRight={3}
      borderRight={1}
      borderColor={grey[300]}
      sx={{
        height: CONTENT_HEIGHT,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          paddingY: 1,
          borderBottom: `1px solid ${grey[200]}`,
          minHeight: 26,
        }}
      >
        <Box sx={{ fontWeight: 500, color: grey[700], fontSize: "24px" }}>
          Sidebar
        </Box>
        <Tooltip title="Close sidebar">
          <IconButton
            onClick={onToggle}
            size="small"
            sx={{
              color: grey[600],
              "&:hover": {
                backgroundColor: grey[100],
                color: grey[900],
              },
            }}
          >
            <MenuOpenIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>
      <Box sx={{ flex: 1, overflow: "auto" }}>{children}</Box>
    </Box>
  )
}
