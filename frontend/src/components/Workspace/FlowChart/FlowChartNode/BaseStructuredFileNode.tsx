import { memo, useEffect, useState } from "react"
import { useDispatch, useSelector } from "react-redux"
import { Handle, Position, NodeProps } from "reactflow"

import FolderIcon from "@mui/icons-material/Folder"
import InsertDriveFileOutlinedIcon from "@mui/icons-material/InsertDriveFileOutlined"
import { Box, Checkbox, Typography, Tooltip, Divider } from "@mui/material"
import Button from "@mui/material/Button"
import { CheckboxProps } from "@mui/material/Checkbox"
import Dialog from "@mui/material/Dialog"
import DialogActions from "@mui/material/DialogActions"
import DialogContent from "@mui/material/DialogContent"
import DialogTitle from "@mui/material/DialogTitle"
import LinearProgress from "@mui/material/LinearProgress"
import { useTheme } from "@mui/material/styles"
import { TreeItem } from "@mui/x-tree-view/TreeItem"
import { TreeView } from "@mui/x-tree-view/TreeView"
import { Action, ThunkAction } from "@reduxjs/toolkit"

import { FileSelect } from "components/Workspace/FlowChart/FlowChartNode/FileSelect"
import { toHandleId } from "components/Workspace/FlowChart/FlowChartNode/FlowChartUtils"
import { NodeContainer } from "components/Workspace/FlowChart/FlowChartNode/NodeContainer"
import { HANDLE_STYLE } from "const/flowchart"
import { deleteFlowNodeById } from "store/slice/FlowElement/FlowElementSlice"
import { NodeIdProps } from "store/slice/FlowElement/FlowElementType"
import { setInputNodeFilePath } from "store/slice/InputNode/InputNodeActions"
import { selectInputNodeDefined } from "store/slice/InputNode/InputNodeSelectors"
import { selectCurrentWorkspaceId } from "store/slice/Workspace/WorkspaceSelector"
import { AppDispatch, RootState } from "store/store"

export type TreeNodeType = TreeDirType | TreeFileType

export interface TreeDirType {
  path: string
  name: string
  isDir: true
  nodes: TreeNodeType[]
  dataType?: string | null
}

export interface TreeFileType {
  path: string
  name: string
  isDir: false
  dataType?: string | null
  shape?: number[] | null
  nbytes?: string
}

export interface FileNodeConfig {
  fileType: string
  handleId: string
  handleType: string
  treeKeyPrefix: string
  selectFilePath: (
    nodeId: string,
  ) => (state: RootState) => string | string[] | undefined
  selectStructurePath: (
    nodeId: string,
  ) => (state: RootState) => string | undefined
  setStructurePath: (params: {
    nodeId: string
    path: string
  }) => Action<unknown>
  getTree: (params: {
    path: string
    workspaceId: number
  }) => ThunkAction<unknown, RootState, unknown, Action<unknown>>
  selectTree: () => (state: RootState) => TreeNodeType[] | undefined
  selectIsLoading: () => (state: RootState) => boolean
}

type ItemSelectProps = {
  open: boolean
  setOpen: (value: boolean) => void
  config: FileNodeConfig
} & NodeIdProps

export function createStructuredFileNode(config: FileNodeConfig) {
  const FileNode = memo(function FileNode(element: NodeProps) {
    const defined = useSelector(selectInputNodeDefined(element.id))
    if (defined) {
      return <FileNodeImple {...element} config={config} />
    } else {
      return null
    }
  })
  FileNode.displayName = `${config.fileType}FileNode`
  return FileNode
}

const FileNodeImple = memo(function FileNodeImple({
  id: nodeId,
  selected,
  config,
}: NodeProps & { config: FileNodeConfig }) {
  const dispatch = useDispatch()
  const filePathRaw = useSelector(config.selectFilePath(nodeId))
  const filePath = Array.isArray(filePathRaw) ? filePathRaw[0] : filePathRaw

  const [open, setOpen] = useState(false)
  const onChangeFilePath = (path: string) => {
    dispatch(setInputNodeFilePath({ nodeId, filePath: path }))
  }

  const onClickDeleteIcon = () => {
    dispatch(deleteFlowNodeById(nodeId))
  }

  return (
    <NodeContainer nodeId={nodeId} selected={selected}>
      <button
        className="flowbutton"
        onClick={onClickDeleteIcon}
        style={{ color: "black", position: "absolute", top: -10, right: 10 }}
      >
        ×
      </button>
      <FileSelect
        nodeId={nodeId}
        onChangeFilePath={(path) => {
          if (!Array.isArray(path)) {
            onChangeFilePath(path)
          }
        }}
        setOpen={setOpen}
        fileType={config.fileType}
        filePath={filePath ?? ""}
      />
      {filePath !== undefined && (
        <ItemSelect
          open={open}
          setOpen={setOpen}
          nodeId={nodeId}
          config={config}
        />
      )}
      <Handle
        type="source"
        position={Position.Right}
        id={toHandleId(nodeId, config.handleId, config.handleType)}
        style={{ ...HANDLE_STYLE }}
      />
    </NodeContainer>
  )
})

const ItemSelect = memo(function ItemSelect({
  nodeId,
  open,
  setOpen,
  config,
}: ItemSelectProps) {
  const dispatch = useDispatch<AppDispatch>()
  const [fileSelect, setFileSelect] = useState("")

  const structureFileName = useSelector(config.selectStructurePath(nodeId))

  const onClickOk = () => {
    dispatch(config.setStructurePath({ nodeId, path: fileSelect }))
    setOpen?.(false)
  }

  const onClickCancel = () => {
    setFileSelect("")
    setOpen?.(false)
  }

  return (
    <>
      <Typography className="selectFilePath" variant="caption">
        {structureFileName
          ? `\u21B3 ${structureFileName}`
          : "No structure is selected."}
      </Typography>
      <Dialog open={open} onClose={() => setOpen(false)} fullWidth>
        <DialogTitle>Select Structure</DialogTitle>
        <Structure
          nodeId={nodeId}
          fileSelect={fileSelect}
          setFileSelect={setFileSelect}
          config={config}
        />
        <DialogActions>
          <Button onClick={onClickCancel} color="primary" variant="outlined">
            cancel
          </Button>
          <Button onClick={onClickOk} variant="contained" autoFocus>
            OK
          </Button>
        </DialogActions>
      </Dialog>
    </>
  )
})

const Structure = memo(function Structure({
  nodeId,
  fileSelect,
  setFileSelect,
  config,
}: NodeIdProps & {
  config: FileNodeConfig
  fileSelect: string
  setFileSelect: (value: string) => void
}) {
  const theme = useTheme()
  return (
    <DialogContent dividers>
      <div
        style={{
          height: 300,
          overflow: "auto",
          marginBottom: theme.spacing(1),
          border: "1px solid",
          padding: theme.spacing(1),
          borderColor: theme.palette.divider,
        }}
      >
        <FileTreeView
          nodeId={nodeId}
          fileSelect={fileSelect}
          setFileSelect={setFileSelect}
          config={config}
        />
      </div>
      <Typography>Selected Path</Typography>
      <Typography variant="subtitle2">{fileSelect || "---"}</Typography>
    </DialogContent>
  )
})

const FileTreeView = memo(function FileTreeView({
  nodeId,
  fileSelect,
  setFileSelect,
  config,
}: NodeIdProps & {
  config: FileNodeConfig
  fileSelect: string
  setFileSelect: (value: string) => void
}) {
  const [tree, isLoading] = useStructuredTree(nodeId, config)
  return (
    <div>
      {isLoading && <LinearProgress />}
      <Box display={"flex"} paddingBottom={1}>
        <Box flexGrow={4}>Structure</Box>
        <Box flexGrow={2}>Type</Box>
        <Box flexGrow={3}>Shape</Box>
        <Box flexGrow={2}>Nbytes</Box>
        <Box flexGrow={1}></Box>
      </Box>
      <Divider />
      <TreeView>
        {tree?.map((node, i) => (
          <TreeNode
            fileSelect={fileSelect}
            setFileSelect={setFileSelect}
            key={`${config.treeKeyPrefix}-${nodeId}-${i}`}
            node={node}
            nodeId={nodeId}
            config={config}
          />
        ))}
      </TreeView>
    </div>
  )
})

interface TreeItemLabelProps {
  isFile: boolean
  shape: number[]
  type: string | null
  label: string
  nbytes?: string
  checkboxProps: CheckboxProps
}

export const TreeItemLabel = memo(function TreeItemLabel({
  isFile = false,
  label,
  shape,
  type,
  nbytes,
  checkboxProps,
}: TreeItemLabelProps) {
  return (
    <Box display="flex" alignItems="center" gap={2}>
      <Tooltip
        title={<span style={{ fontSize: 14 }}>{label}</span>}
        placement={"left"}
      >
        <Box
          width={isFile ? "25%" : "22%"}
          overflow={"hidden"}
          textOverflow={"ellipsis"}
        >
          {label}
        </Box>
      </Tooltip>
      <Box width={"15%"}>{type}</Box>
      <Box width={"25%"}>{shape ? `(${shape.join(", ")})` : ""}</Box>
      <Box width={"15%"}>{nbytes}</Box>
      <Box>
        <Checkbox
          {...checkboxProps}
          disableRipple
          size="small"
          sx={{
            marginRight: "4px",
            padding: "2px",
          }}
        />
      </Box>
    </Box>
  )
})

interface TreeNodeProps extends NodeIdProps {
  setFileSelect?: (value: string) => void
  fileSelect?: string
  node: TreeNodeType
  config: FileNodeConfig
}

const TreeNode = memo(function TreeNode({
  node,
  nodeId,
  setFileSelect,
  fileSelect,
  config,
}: TreeNodeProps) {
  const dispatch = useDispatch()
  const structureFileName = useSelector(config.selectStructurePath(nodeId))
  useEffect(() => {
    if (!structureFileName) return
    setFileSelect?.(structureFileName)
    //eslint-disable-next-line
  }, [dispatch, structureFileName])
  const onClickFile = (path: string) => {
    setFileSelect?.(path === fileSelect ? "" : path)
  }

  if (node.isDir) {
    // Directory
    return (
      <TreeItem
        icon={<FolderIcon htmlColor="skyblue" />}
        nodeId={node.path}
        label={node.name}
      >
        {(node as TreeDirType).nodes.map((childNode, i) => (
          <TreeNode
            setFileSelect={setFileSelect}
            fileSelect={fileSelect}
            node={childNode}
            key={i}
            nodeId={nodeId}
            config={config}
          />
        ))}
      </TreeItem>
    )
  } else {
    // File
    return (
      <TreeItem
        icon={<InsertDriveFileOutlinedIcon fontSize="small" />}
        nodeId={node.path}
        label={
          <TreeItemLabel
            isFile={true}
            label={node.name}
            type={node.dataType || null}
            shape={(node as TreeFileType).shape || []}
            nbytes={(node as TreeFileType).nbytes}
            checkboxProps={{
              checked: fileSelect === node.path,
            }}
          />
        }
        onClick={() => onClickFile(node.path)}
      />
    )
  }
})

function useStructuredTree(
  nodeId: string,
  config: FileNodeConfig,
): [TreeNodeType[] | undefined, boolean] {
  const dispatch = useDispatch<AppDispatch>()
  const tree = useSelector(config.selectTree())
  const isLoading = useSelector(config.selectIsLoading())
  const filePathRaw = useSelector(config.selectFilePath(nodeId))
  const filePath = Array.isArray(filePathRaw) ? filePathRaw[0] : filePathRaw
  const workspaceId = useSelector(selectCurrentWorkspaceId)
  useEffect(() => {
    if (workspaceId && !isLoading && filePath) {
      dispatch(
        config.getTree({
          path: filePath as string,
          workspaceId: Number(workspaceId),
        }),
      )
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, filePath])
  return [tree, isLoading]
}
