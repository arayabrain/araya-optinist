import { memo, useEffect, useState } from "react"
import { useDispatch, useSelector } from "react-redux"
import { Handle, Position, NodeProps } from "reactflow"

import FolderIcon from "@mui/icons-material/Folder"
import InsertDriveFileOutlinedIcon from "@mui/icons-material/InsertDriveFileOutlined"
import { Box, Typography, Divider } from "@mui/material"
import Button from "@mui/material/Button"
import Dialog from "@mui/material/Dialog"
import DialogActions from "@mui/material/DialogActions"
import DialogContent from "@mui/material/DialogContent"
import DialogTitle from "@mui/material/DialogTitle"
import LinearProgress from "@mui/material/LinearProgress"
import { useTheme } from "@mui/material/styles"
import { TreeItem } from "@mui/x-tree-view/TreeItem"
import { TreeView } from "@mui/x-tree-view/TreeView"
import { Action, ThunkAction } from "@reduxjs/toolkit"

import { TreeItemLabel as BaseTreeItemLabel } from "components/Workspace/FlowChart/Dialog/StructureItemSelectDialog"
import { TreeNodeType } from "components/Workspace/FlowChart/FlowChartNode/BaseStructuredFileNode"
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
import { arrayEqualityFn } from "utils/EqualityUtils"

export interface BatchFileNodeConfig {
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
  config: BatchFileNodeConfig
  filePath: string[] | undefined
} & NodeIdProps

export function createBatchStructuredFileNode(config: BatchFileNodeConfig) {
  const BatchFileNode = memo(function BatchFileNode(element: NodeProps) {
    const defined = useSelector(selectInputNodeDefined(element.id))
    if (defined) {
      return <BatchFileNodeImple {...element} config={config} />
    } else {
      return null
    }
  })
  BatchFileNode.displayName = `Batch${config.fileType}FileNode`
  return BatchFileNode
}

const BatchFileNodeImple = memo(function BatchFileNodeImple({
  id: nodeId,
  selected,
  config,
}: NodeProps & { config: BatchFileNodeConfig }) {
  const dispatch = useDispatch()
  const filePath = useSelector(config.selectFilePath(nodeId), (a, b) =>
    a != null && b != null && Array.isArray(a) && Array.isArray(b)
      ? arrayEqualityFn(a, b)
      : a === b,
  )

  const [open, setOpen] = useState(false)
  const onChangeFilePath = (path: string[]) => {
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
        multiSelect
        onChangeFilePath={(path) => {
          if (Array.isArray(path)) {
            onChangeFilePath(path)
          }
        }}
        setOpen={setOpen}
        fileType={config.fileType}
        filePath={
          Array.isArray(filePath) ? filePath : filePath ? [filePath] : []
        }
      />
      {filePath !== undefined &&
        Array.isArray(filePath) &&
        filePath.length > 0 && (
          <ItemSelect
            open={open}
            setOpen={setOpen}
            nodeId={nodeId}
            filePath={filePath}
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
  filePath,
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

  const displayText = structureFileName
    ? `Structure: ${structureFileName}`
    : "No structure is selected."

  return (
    <>
      <Typography className="selectFilePath" variant="caption">
        {displayText}
      </Typography>
      <Dialog open={open} onClose={() => setOpen(false)} fullWidth>
        <DialogTitle>
          {"Select File Structure (Applied to all files)"}
        </DialogTitle>
        <Structure
          nodeId={nodeId}
          fileSelect={fileSelect}
          setFileSelect={setFileSelect}
          filePath={filePath}
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
  filePath,
  config,
}: NodeIdProps & {
  config: BatchFileNodeConfig
  fileSelect: string
  setFileSelect: (value: string) => void
  filePath: string[] | undefined
}) {
  const theme = useTheme()
  return (
    <DialogContent dividers>
      {filePath && filePath.length > 0 && (
        <Typography
          variant="caption"
          color="textSecondary"
          style={{ marginBottom: theme.spacing(1), display: "block" }}
        >
          Using structure of first file: {`"${filePath[0]}"`}
        </Typography>
      )}
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
        {filePath && filePath.length > 0 && (
          <FileTreeView
            nodeId={nodeId}
            fileSelect={fileSelect}
            setFileSelect={setFileSelect}
            filePath={filePath[0]}
            config={config}
          />
        )}
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
  filePath,
  config,
}: NodeIdProps & {
  config: BatchFileNodeConfig
  fileSelect: string
  setFileSelect: (value: string) => void
  filePath: string
}) {
  const [tree, isLoading] = useBatchStructuredTree(nodeId, filePath, config)
  const hasDetailedInfo = tree && tree.length > 0 && "shape" in tree[0]

  return (
    <div>
      {isLoading && <LinearProgress />}
      {hasDetailedInfo && (
        <>
          <Box display={"flex"} paddingBottom={1}>
            <Box flexGrow={4}>Structure</Box>
            <Box flexGrow={2}>Type</Box>
            <Box flexGrow={3}>Shape</Box>
            <Box flexGrow={2}>Nbytes</Box>
            <Box flexGrow={1}></Box>
          </Box>
          <Divider />
        </>
      )}
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

interface TreeNodeProps extends NodeIdProps {
  setFileSelect?: (value: string) => void
  fileSelect?: string
  node: TreeNodeType
  config: BatchFileNodeConfig
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
  }, [structureFileName, setFileSelect])

  const onClickFile = (path: string) => {
    setFileSelect?.(path === fileSelect ? "" : path)
    dispatch(config.setStructurePath({ nodeId, path }))
  }

  if (node.isDir) {
    return (
      <TreeItem
        icon={<FolderIcon htmlColor="skyblue" />}
        nodeId={node.path}
        label={node.name}
      >
        {node.nodes.map((childNode, i) => (
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
    const hasDetailedInfo =
      "shape" in node || "dataType" in node || "nbytes" in node

    if (hasDetailedInfo) {
      return (
        <TreeItem
          icon={<InsertDriveFileOutlinedIcon fontSize="small" />}
          nodeId={node.path}
          label={
            <BaseTreeItemLabel
              isFile={true}
              label={node.name}
              type={node.dataType || null}
              shape={node.shape || []}
              nbytes={node.nbytes}
              checkboxProps={{
                checked: fileSelect === node.path,
              }}
            />
          }
          onClick={() => onClickFile(node.path)}
        />
      )
    } else {
      return (
        <TreeItem
          icon={<InsertDriveFileOutlinedIcon fontSize="small" />}
          nodeId={node.path}
          label={
            node.name +
            ("shape" in node && node.shape ? `   (shape=${node.shape}` : "") +
            ("nbytes" in node && node.nbytes
              ? `, nbytes=${node.nbytes})`
              : "shape" in node && node.shape
                ? ")"
                : "")
          }
          onClick={() => onClickFile(node.path)}
        />
      )
    }
  }
})

function useBatchStructuredTree(
  _nodeId: string,
  filePath: string | undefined,
  config: BatchFileNodeConfig,
): [TreeNodeType[] | undefined, boolean] {
  const dispatch = useDispatch<AppDispatch>()
  const tree = useSelector(config.selectTree())
  const isLoading = useSelector(config.selectIsLoading())
  const workspaceId = useSelector(selectCurrentWorkspaceId)

  useEffect(() => {
    if (workspaceId && filePath) {
      dispatch(config.getTree({ path: filePath, workspaceId }))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, filePath])

  return [tree, isLoading]
}
