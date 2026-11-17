import { Fragment, useState } from "react"

import ExpandLessIcon from "@mui/icons-material/ExpandLess"
import ExpandMoreIcon from "@mui/icons-material/ExpandMore"
import VisibilityIcon from "@mui/icons-material/Visibility"
import VisibilityOffIcon from "@mui/icons-material/VisibilityOff"
import {
  Box,
  Chip,
  Collapse,
  List,
  ListItemButton,
  ListItemText,
  Paper,
  Switch,
  Typography,
} from "@mui/material"

import {
  BaseDisplayDataView,
  VisualizationItemData,
} from "components/Dataview/BaseNodesView"

export type OutputsSection = {
  nodeId: string
  nodeName: string
  items: VisualizationItemData[]
}

type OutputsOutlineProps = {
  sections: OutputsSection[]
  isPublic: (item: VisualizationItemData) => boolean
  onToggle: (item: VisualizationItemData, isPublic: boolean) => void
  disabled: boolean
}

const countPublic = (
  items: VisualizationItemData[],
  isPublic: OutputsOutlineProps["isPublic"],
) => items.filter(isPublic).length

// The owner's view: every output with its switch on the left, one plot on the right
const OutputsOutline = ({
  sections,
  isPublic,
  onToggle,
  disabled,
}: OutputsOutlineProps) => {
  const all = sections.flatMap((section) => section.items)
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const [selectedKey, setSelectedKey] = useState(all[0]?.itemKey)
  const selected = all.find((item) => item.itemKey === selectedKey)

  return (
    <Box display="flex" gap={2} sx={{ height: "calc(90vh - 150px)" }}>
      <Paper
        variant="outlined"
        sx={{ width: 400, flexShrink: 0, overflow: "auto" }}
      >
        <Box
          px={2}
          py={1.5}
          display="flex"
          justifyContent="space-between"
          alignItems="center"
        >
          <Typography variant="subtitle1" fontWeight="bold">
            Outputs
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Public {countPublic(all, isPublic)} / {all.length}
          </Typography>
        </Box>
        <List dense disablePadding>
          {sections.map((section) => (
            <Fragment key={section.nodeId}>
              <ListItemButton
                onClick={() =>
                  setCollapsed({
                    ...collapsed,
                    [section.nodeId]: !collapsed[section.nodeId],
                  })
                }
                sx={{ bgcolor: "action.hover" }}
              >
                {collapsed[section.nodeId] ? (
                  <ExpandMoreIcon />
                ) : (
                  <ExpandLessIcon />
                )}
                <ListItemText
                  sx={{ ml: 1 }}
                  primary={section.nodeName}
                  secondary={section.nodeId}
                />
                <Typography variant="caption" color="text.secondary">
                  {countPublic(section.items, isPublic)} /{" "}
                  {section.items.length} public
                </Typography>
              </ListItemButton>
              <Collapse in={!collapsed[section.nodeId]}>
                {section.items.map((item) => (
                  <ListItemButton
                    key={item.itemKey}
                    selected={item.itemKey === selectedKey}
                    onClick={() => setSelectedKey(item.itemKey)}
                    sx={{ pl: 6, opacity: isPublic(item) ? 1 : 0.6 }}
                  >
                    <ListItemText
                      primary={item.title}
                      secondary={item.dataType}
                    />
                    <Switch
                      size="small"
                      checked={isPublic(item)}
                      disabled={disabled}
                      onClick={(e) => e.stopPropagation()}
                      onChange={(_, checked) => onToggle(item, checked)}
                      inputProps={{ "aria-label": `Publish ${item.title}` }}
                    />
                  </ListItemButton>
                ))}
              </Collapse>
            </Fragment>
          ))}
        </List>
      </Paper>
      <Paper
        variant="outlined"
        sx={{ flex: 1, p: 2, overflow: "auto", minWidth: 0 }}
      >
        {selected && (
          <>
            <Box display="flex" alignItems="center" gap={1} mb={1}>
              <Typography variant="h6">{selected.title}</Typography>
              <Chip size="small" label={selected.dataType} />
              <Chip
                size="small"
                color={isPublic(selected) ? "success" : "default"}
                icon={
                  isPublic(selected) ? (
                    <VisibilityIcon />
                  ) : (
                    <VisibilityOffIcon />
                  )
                }
                label={isPublic(selected) ? "Public" : "Hidden from public"}
              />
            </Box>
            <BaseDisplayDataView
              key={selected.itemKey}
              nodeId={selected.nodeId}
              filePath={selected.filePath}
              dataType={selected.dataType}
            />
          </>
        )}
      </Paper>
    </Box>
  )
}

export default OutputsOutline
