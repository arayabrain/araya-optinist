import React, {
  memo,
  useContext,
  useEffect,
  useState,
  MouseEvent,
  useCallback,
  useRef,
} from "react"
import { useDispatch, useSelector } from "react-redux"

import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  DragEndEvent,
} from "@dnd-kit/core"
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
  useSortable,
} from "@dnd-kit/sortable"
import { CSS } from "@dnd-kit/utilities"
import AutorenewIcon from "@mui/icons-material/Autorenew"
import CloseIcon from "@mui/icons-material/Close"
import DeleteIcon from "@mui/icons-material/Delete"
import DragIndicatorIcon from "@mui/icons-material/DragIndicator"
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline"
import FolderIcon from "@mui/icons-material/Folder"
import InsertDriveFileOutlinedIcon from "@mui/icons-material/InsertDriveFileOutlined"
import { Divider, IconButton, Tooltip, Chip } from "@mui/material"
import Box from "@mui/material/Box"
import Button from "@mui/material/Button"
import Checkbox, { CheckboxProps } from "@mui/material/Checkbox"
import Dialog from "@mui/material/Dialog"
import DialogActions from "@mui/material/DialogActions"
import DialogContent from "@mui/material/DialogContent"
import DialogTitle from "@mui/material/DialogTitle"
import LinearProgress from "@mui/material/LinearProgress"
import { useTheme, styled } from "@mui/material/styles"
import Typography from "@mui/material/Typography"
import { TreeItem } from "@mui/x-tree-view/TreeItem"
import { TreeView } from "@mui/x-tree-view/TreeView"

import { FILE_TREE_TYPE, FILE_TREE_TYPE_SET } from "api/files/Files"
import { ConfirmDialog } from "components/common/ConfirmDialog"
import { DialogContext } from "components/Workspace/FlowChart/Dialog/DialogContext"
import { deleteFile, getFilesTree } from "store/slice/FilesTree/FilesTreeAction"
import {
  selectFilesIsLatest,
  selectFilesIsLoading,
  selectFilesTreeNodes,
} from "store/slice/FilesTree/FilesTreeSelectors"
import { TreeNodeType } from "store/slice/FilesTree/FilesTreeType"
import { getNodeByPath } from "store/slice/FilesTree/FilesTreeUtils"
import { updateShape } from "store/slice/FileUploader/FileUploaderActions"
import { selectPipelineLatestUid } from "store/slice/Pipeline/PipelineSelectors"
import { selectCurrentWorkspaceId } from "store/slice/Workspace/WorkspaceSelector"
import { AppDispatch } from "store/store"

// Constants
const COLUMN_MIN_WIDTH = 20
const COLUMN_MAX_WIDTH = 80
const COLUMN_DEFAULT_WIDTH = 50

// Styled components (5+ attributes or commonly used)
const StyledColumnResizer = styled(Box)({
  width: "10px",
  cursor: "col-resize",
  position: "absolute",
  top: 0,
  bottom: "6px",
  zIndex: 10,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  "&::before": {
    content: "''",
    width: "1px",
    height: "100%",
    backgroundColor: "rgba(0, 0, 0, 0.2)",
    display: "block",
  },
  "&:hover::before": {
    backgroundColor: "rgba(0, 0, 0, 0.4)",
    width: "2px",
  },
})

const StyledCheckbox = styled(Checkbox)({
  marginRight: "4px",
  padding: "2px",
  minWidth: 24,
})

// Common style objects for reuse
const ellipsisStyle = {
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
} as const

type FileSelectDialogProps = {
  initialFilePath: string[] | string
  onClickOk: (path: string[] | string) => void
  fileType?: FILE_TREE_TYPE
  title?: string
  open: boolean
  onClickCancel: () => void
  multiSelect: boolean
}

export const FileSelectDialog = memo(function FileSelectDialog({
  open,
  initialFilePath,
  onClickCancel,
  onClickOk,
  title,
  fileType = FILE_TREE_TYPE_SET.ALL,
  multiSelect,
}: FileSelectDialogProps) {
  useEffect(() => {
    setSelectedFilePath(initialFilePath)
  }, [initialFilePath])
  const { onOpenClearWorkflowIdDialog } = useContext(DialogContext)
  const currentWorkflowId = useSelector(selectPipelineLatestUid)
  const [selectedFilePath, setSelectedFilePath] = useState(initialFilePath)

  const onCancel = () => {
    setSelectedFilePath(initialFilePath) // 選択内容を反映させない
    onClickCancel()
  }
  const onOk = () => {
    if (currentWorkflowId != null) {
      onOpenClearWorkflowIdDialog({
        open: true,
        handleOk: () => {
          onClickOk(selectedFilePath)
        },
        handleCancel: () => onCancel(),
      })
    } else {
      onClickOk(selectedFilePath)
    }
  }
  const theme = useTheme()

  return (
    <Dialog open={open} onClose={onCancel} fullWidth>
      <DialogTitle>{title ?? "Select File"}</DialogTitle>
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
            setSelectedFilePath={setSelectedFilePath}
            multiSelect={multiSelect}
            fileType={fileType}
            selectedFilePath={selectedFilePath}
          />
        </div>
        <Box display="flex" alignItems="center" gap={1}>
          <Typography variant="subtitle1">
            {Array.isArray(selectedFilePath) && selectedFilePath.length === 0
              ? "No Selected File"
              : "Selected Files"}
          </Typography>
          {Array.isArray(selectedFilePath) && selectedFilePath.length > 0 && (
            <Chip
              label={selectedFilePath.length}
              size="small"
              color="primary"
              variant="outlined"
              sx={{ fontSize: "0.75rem", height: "20px", fontWeight: "bold" }}
            />
          )}
        </Box>
        <FilePathSelectedListView
          path={selectedFilePath}
          setSelectedFilePath={setSelectedFilePath}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel} variant="outlined">
          cancel
        </Button>
        <Button onClick={onOk} variant="contained">
          OK
        </Button>
      </DialogActions>
    </Dialog>
  )
})

interface FileTreeViewProps {
  setSelectedFilePath: (path: string[] | string) => void
  selectedFilePath: string[] | string
  multiSelect: boolean
  fileType: FILE_TREE_TYPE
}

const FileTreeView = memo(function FileTreeView({
  setSelectedFilePath,
  selectedFilePath,
  fileType,
  multiSelect,
}: FileTreeViewProps) {
  const [tree, isLoading] = useFileTree(fileType)
  const [initialized, setInitialized] = useState(false)
  const [columnWidth, setColumnWidth] = useState(COLUMN_DEFAULT_WIDTH) // Column width in percentage
  const [isDragging, setIsDragging] = useState(false)

  // Helper function to check if a file exists in the tree
  const isFileInTree = (path: string, tree: TreeNodeType[] | null): boolean => {
    if (!tree) return false
    const checkNode = (node: TreeNodeType): boolean => {
      if (node.path === path) return true
      if (node.isDir && node.nodes) {
        return node.nodes.some(checkNode)
      }
      return false
    }
    return tree.some(checkNode)
  }

  // Effect to remove selected file if it's not in the tree
  useEffect(() => {
    if (!initialized && tree) {
      if (Array.isArray(selectedFilePath)) {
        const validPaths = selectedFilePath.filter((path) =>
          isFileInTree(path, tree),
        )
        if (validPaths.length !== selectedFilePath.length) {
          setSelectedFilePath(validPaths)
        }
      } else if (selectedFilePath && !isFileInTree(selectedFilePath, tree)) {
        setSelectedFilePath([])
      }
      setInitialized(true) // Prevents re-running on every render
    }
  }, [tree, initialized, selectedFilePath, setSelectedFilePath])

  // multiSelectでチェックボックスを使用する時用のハンドラ
  const onCheckFile = (path: string) => {
    if (Array.isArray(selectedFilePath)) {
      if (selectedFilePath.includes(path)) {
        setSelectedFilePath(
          selectedFilePath.filter((selectedPath) => path !== selectedPath),
        )
      } else {
        setSelectedFilePath(selectedFilePath.concat(path))
      }
    }
  }
  const onCheckDir = (path: string, checked: boolean) => {
    if (tree != null && Array.isArray(selectedFilePath)) {
      const node = getNodeByPath(path, tree)
      if (node != null && node.isDir) {
        const childrenFilePathList = node.nodes
          .filter((node) => !node.isDir)
          .map((node) => node.path)
        if (checked) {
          setSelectedFilePath(
            // concat時の重複を削除
            Array.from(new Set(selectedFilePath.concat(childrenFilePathList))),
          )
        } else {
          setSelectedFilePath(
            selectedFilePath.filter(
              (selectedPath) => !childrenFilePathList.includes(selectedPath),
            ),
          )
        }
      }
    }
  }

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault()
    setIsDragging(true)
  }

  const handleMouseUp = useCallback(() => {
    setIsDragging(false)
  }, [])

  const containerRef = useRef<HTMLDivElement>(null)

  const handleMouseMove = useCallback(
    (e: Event) => {
      if (!isDragging || !containerRef.current) return
      const mouseEvent = e as unknown as MouseEvent
      const rect = containerRef.current.getBoundingClientRect()
      const newWidth = ((mouseEvent.clientX - rect.left) / rect.width) * 100
      setColumnWidth(
        Math.max(COLUMN_MIN_WIDTH, Math.min(COLUMN_MAX_WIDTH, newWidth)),
      )
    },
    [isDragging],
  )

  useEffect(() => {
    if (isDragging) {
      document.addEventListener("mousemove", handleMouseMove)
      document.addEventListener("mouseup", handleMouseUp)
      document.body.style.cursor = "col-resize"
      document.body.style.userSelect = "none"
      return () => {
        document.removeEventListener("mousemove", handleMouseMove)
        document.removeEventListener("mouseup", handleMouseUp)
        document.body.style.cursor = ""
        document.body.style.userSelect = ""
      }
    }
    return undefined
  }, [isDragging, handleMouseMove, handleMouseUp])

  return (
    <div
      ref={containerRef}
      style={{ display: "flex", flexDirection: "column", height: "100%" }}
    >
      {isLoading && <LinearProgress />}
      <>
        <Box
          sx={{
            display: "flex",
            position: "sticky",
            top: 0,
            backgroundColor: "background.paper",
            zIndex: 5,
            userSelect: "none",
            paddingBottom: "6px",
          }}
        >
          <Typography sx={{ width: `${columnWidth}%`, ...ellipsisStyle }}>
            Files
          </Typography>
          <StyledColumnResizer
            sx={{ left: `calc(${columnWidth}% - 5px)` }}
            onMouseDown={handleMouseDown}
          />
          {fileType === FILE_TREE_TYPE_SET.IMAGE && (
            <Typography sx={{ flex: 1, ...ellipsisStyle }} marginLeft={2}>
              Shapes
            </Typography>
          )}
        </Box>
        <Divider />
      </>
      <Box sx={{ flex: 1, overflow: "auto" }}>
        <TreeView disableSelection={multiSelect} multiSelect={multiSelect}>
          {tree?.map((node) => (
            <TreeNode
              fileType={fileType}
              key={node.name}
              node={node}
              selectedFilePath={selectedFilePath}
              multiSelect={multiSelect}
              onCheckDir={onCheckDir}
              onCheckFile={onCheckFile}
              setSelectedFilePath={setSelectedFilePath}
              columnWidth={columnWidth}
            />
          ))}
        </TreeView>
      </Box>
    </div>
  )
})

interface TreeNodeProps {
  fileType: FILE_TREE_TYPE
  node: TreeNodeType
  selectedFilePath: string[] | string
  multiSelect: boolean
  onCheckDir: (path: string, checked: boolean) => void
  onCheckFile: (path: string) => void
  setSelectedFilePath: (path: string[] | string) => void
  columnWidth?: number
}

const TreeNode = memo(function TreeNode({
  fileType,
  node,
  selectedFilePath,
  multiSelect,
  onCheckDir,
  onCheckFile,
  setSelectedFilePath,
  columnWidth = COLUMN_DEFAULT_WIDTH,
}: TreeNodeProps) {
  if (node.isDir) {
    const allChecked =
      Array.isArray(selectedFilePath) &&
      node.nodes
        .filter((node) => !node.isDir)
        .map((node) => node.path)
        .every((filePath) => selectedFilePath.includes(filePath))
    const allNotChecked =
      Array.isArray(selectedFilePath) &&
      node.nodes
        .filter((node) => !node.isDir)
        .map((node) => node.path)
        .every((filePath) => !selectedFilePath.includes(filePath))
    const indeterminate = !(allChecked || allNotChecked)
    return (
      <TreeItem
        icon={<FolderIcon htmlColor="skyblue" />}
        nodeId={node.path}
        label={
          multiSelect && node.nodes.filter((node) => !node.isDir).length > 0 ? (
            <TreeItemLabel
              multiSelect={multiSelect}
              isDir={node.isDir}
              fileType={fileType}
              shape={node.shape}
              label={node.name}
              checkboxProps={{
                indeterminate,
                checked: allChecked,
                onClick: (e) => {
                  e.stopPropagation() // on/offのクリックにつられてTreeを開閉させないようにする
                },
                onChange: (e) => onCheckDir(node.path, e.target.checked),
              }}
              setSelectedFilePath={setSelectedFilePath}
              selectedFilePath={selectedFilePath}
              columnWidth={columnWidth}
            />
          ) : (
            node.name
          )
        }
      >
        {node.nodes.map((childNode, i) => (
          <TreeNode
            fileType={fileType}
            node={childNode}
            selectedFilePath={selectedFilePath}
            key={i}
            multiSelect={multiSelect}
            onCheckDir={onCheckDir}
            onCheckFile={onCheckFile}
            setSelectedFilePath={setSelectedFilePath}
            columnWidth={columnWidth}
          />
        ))}
      </TreeItem>
    )
  } else {
    return (
      <TreeItem
        icon={<InsertDriveFileOutlinedIcon fontSize="small" />}
        nodeId={node.path}
        label={
          <TreeItemLabel
            multiSelect={multiSelect}
            isDir={node.isDir}
            fileType={fileType}
            shape={node.shape}
            label={node.name}
            checkboxProps={{
              checked: multiSelect
                ? Array.isArray(selectedFilePath) &&
                  selectedFilePath.includes(node.path)
                : selectedFilePath === node.path,
              onChange: (e) => {
                e.stopPropagation()

                if (multiSelect) {
                  if (Array.isArray(selectedFilePath)) {
                    if (selectedFilePath.includes(node.path)) {
                      setSelectedFilePath(
                        selectedFilePath.filter((f) => f !== node.path),
                      )
                    } else {
                      setSelectedFilePath([...selectedFilePath, node.path])
                    }
                  }
                } else {
                  if (selectedFilePath === node.path) {
                    setSelectedFilePath("")
                  } else {
                    setSelectedFilePath(node.path)
                  }
                }
              },
            }}
            setSelectedFilePath={setSelectedFilePath}
            selectedFilePath={selectedFilePath}
            columnWidth={columnWidth}
          />
        }
      />
    )
  }
})

interface TreeItemLabelProps {
  fileType: FILE_TREE_TYPE
  shape: number[]
  label: string
  checkboxProps: CheckboxProps
  isDir?: boolean
  setSelectedFilePath: (path: string[] | string) => void
  selectedFilePath: string[] | string
  multiSelect: boolean
  columnWidth?: number
}

export const TreeItemLabel = memo(function TreeItemLabel({
  fileType,
  shape,
  label,
  isDir,
  checkboxProps,
  setSelectedFilePath,
  selectedFilePath,
  multiSelect,
  columnWidth = COLUMN_DEFAULT_WIDTH,
}: TreeItemLabelProps) {
  const dispatch = useDispatch<AppDispatch>()
  const workspaceId = useSelector(selectCurrentWorkspaceId)
  const [deleteConfirmDialogOpen, setDeleteConfirmDialogOpen] = useState(false)
  const onUpdate = useCallback(
    (event: MouseEvent, fileName: string) => {
      if (!workspaceId) return
      event.stopPropagation()
      dispatch(updateShape({ workspaceId, fileName }))
    },
    [dispatch, workspaceId],
  )
  const onOpenDeleteConfirmDialog = useCallback(
    (event: MouseEvent) => {
      if (!workspaceId) return
      event.stopPropagation()
      setDeleteConfirmDialogOpen(true)
    },
    [workspaceId],
  )
  const onDelete = useCallback(
    (event: MouseEvent, fileName: string) => {
      if (!workspaceId) return
      event.stopPropagation()

      // Remove the file from selectedFile state
      if (Array.isArray(selectedFilePath)) {
        setSelectedFilePath(
          selectedFilePath.filter((file) => file !== fileName),
        )
      }

      dispatch(deleteFile({ workspaceId, fileName, fileType }))
    },
    [dispatch, fileType, selectedFilePath, setSelectedFilePath, workspaceId],
  )

  return (
    <Box
      sx={{
        "&:hover": {
          backgroundColor: "transparent",
        },
        cursor: "default",
      }}
    >
      <Box
        height={24}
        display="flex"
        justifyContent="space-between"
        alignItems="center"
      >
        <Tooltip
          title={<span style={{ fontSize: 14 }}>{label}</span>}
          placement={"left-start"}
        >
          <Box
            sx={{
              width: `${columnWidth}%`,
              ...ellipsisStyle,
              whiteSpace: "pre",
            }}
          >
            {label}
          </Box>
        </Tooltip>
        {fileType === FILE_TREE_TYPE_SET.IMAGE ? (
          <>
            <Box flex={1} marginLeft={2} alignItems="center" sx={ellipsisStyle}>
              {!isDir ? (
                !shape ? (
                  <Tooltip
                    title={
                      <span style={{ fontSize: 14 }}>
                        parsing image shape failed
                      </span>
                    }
                    placement={"right"}
                  >
                    <ErrorOutlineIcon color={"error"} />
                  </Tooltip>
                ) : (
                  <span>{`(${shape.join(", ")})`}</span>
                )
              ) : null}
            </Box>
          </>
        ) : null}
        <Box display="flex" alignItems="center">
          <Box>
            <StyledCheckbox {...checkboxProps} size="small" disableRipple />
          </Box>

          {!isDir && multiSelect ? (
            <IconButton
              sx={{ minWidth: 24 }}
              onClick={(event) => onUpdate(event, label)}
            >
              <AutorenewIcon />
            </IconButton>
          ) : null}
          <IconButton
            sx={{ minWidth: 24 }}
            color="error"
            onClick={(event) => {
              event.stopPropagation()
              onOpenDeleteConfirmDialog(event)
            }}
            disabled={checkboxProps.checked}
            data-testid="DeleteIconBtn"
          >
            <DeleteIcon />
          </IconButton>
        </Box>
      </Box>
      <ConfirmDialog
        open={deleteConfirmDialogOpen}
        setOpen={setDeleteConfirmDialogOpen}
        onConfirm={() => {
          onDelete({ stopPropagation: () => {} } as MouseEvent, label)
          setDeleteConfirmDialogOpen(false)
        }}
        title="Are you sure you want to delete this item?"
        content={`${label}`}
        confirmLabel="delete"
        iconType="warning"
      />
    </Box>
  )
})

interface SortableItemProps {
  id: string
  text: string
  onRemove: (text: string) => void
}

const SortableItem = memo(function SortableItem({
  id,
  text,
  onRemove,
}: SortableItemProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  return (
    <li
      ref={setNodeRef}
      style={{
        ...style,
        marginBottom: "3px",
        listStyleType: "none",
        marginLeft: "8px",
        cursor: isDragging ? "grabbing" : "grab",
      }}
    >
      <span
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          width: "100%",
        }}
      >
        <span style={{ display: "flex", alignItems: "center", flex: 1 }}>
          <DragIndicatorIcon
            {...attributes}
            {...listeners}
            style={{
              width: "20px",
              height: "20px",
              marginRight: "8px",
              cursor: isDragging ? "grabbing" : "grab",
              color: "#999",
            }}
          />
          <span style={{ flex: 1 }}>{text}</span>
        </span>
        <IconButton style={{ padding: "0" }} onClick={() => onRemove(text)}>
          <CloseIcon style={{ width: "15px", height: "15px" }} />
        </IconButton>
      </span>
    </li>
  )
})

interface FilePathProps {
  path: string | string[]
  setSelectedFilePath: (path: string[] | string) => void
}

const FilePathSelectedListView = memo(function FilePathSelectedListView({
  path,
  setSelectedFilePath,
}: FilePathProps) {
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  )

  const handleRemoveFile = (fileToRemove: string) => {
    if (Array.isArray(path)) {
      setSelectedFilePath(path.filter((file) => file !== fileToRemove))
    }
  }

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event

    if (active.id !== over?.id && Array.isArray(path)) {
      const oldIndex = path.indexOf(active.id as string)
      const newIndex = path.indexOf(over?.id as string)

      if (oldIndex !== -1 && newIndex !== -1) {
        setSelectedFilePath(arrayMove(path, oldIndex, newIndex))
      }
    }
  }

  return (
    <Typography variant="subtitle2">
      {path ? (
        Array.isArray(path) ? (
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <SortableContext
              items={path}
              strategy={verticalListSortingStrategy}
            >
              <ul style={{ padding: 0, margin: 0, listStyleType: "none" }}>
                {path.map((text) => (
                  <SortableItem
                    key={text}
                    id={text}
                    text={text}
                    onRemove={handleRemoveFile}
                  />
                ))}
              </ul>
            </SortableContext>
          </DndContext>
        ) : (
          path
        )
      ) : (
        "---"
      )}
    </Typography>
  )
})

function useFileTree(
  fileType: FILE_TREE_TYPE,
): [TreeNodeType[] | undefined, boolean] {
  const dispatch = useDispatch<AppDispatch>()
  const tree = useSelector(selectFilesTreeNodes(fileType))
  const isLatest = useSelector(selectFilesIsLatest(fileType))
  const isLoading = useSelector(selectFilesIsLoading(fileType))
  const workspaceId = useSelector(selectCurrentWorkspaceId)
  useEffect(() => {
    if (workspaceId && !isLatest && !isLoading) {
      dispatch(getFilesTree({ workspaceId, fileType }))
    }
  }, [workspaceId, isLatest, isLoading, fileType, dispatch])
  return [tree, isLoading]
}
