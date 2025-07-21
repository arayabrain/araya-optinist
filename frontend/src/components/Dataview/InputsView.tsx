import { ReactElement } from "react"
import { useSelector } from "react-redux"

import BaseNodesView, {
  ListItem,
  ListItemText,
} from "components/Dataview/BaseNodesView"
import { selectNodeLabelById } from "store/slice/FlowElement/FlowElementSelectors"
import { getFileName } from "store/slice/FlowElement/FlowElementUtils"
import { selectInputNode } from "store/slice/InputNode/InputNodeSelectors"
import { RootState } from "store/store"

type InputsViewProps = {
  open: boolean
  workspaceId: number | undefined
  uid: string | undefined
  handleClose: () => void
}

const InputsView = ({
  open,
  workspaceId,
  uid,
  handleClose,
}: InputsViewProps) => {
  const inputNodeData = useSelector((state: RootState) => {
    const inputNodes = selectInputNode(state)
    const filteredInputNodes = Object.entries(inputNodes)
      .map(([nodeId, inputNode]) => ({
        nodeId,
        filePath: inputNode.selectedFilePath,
        fileType: inputNode.fileType,
        nodeName: selectNodeLabelById(nodeId)(state) || nodeId,
      }))
      .filter(({ filePath }) => filePath != null)
    return filteredInputNodes
  })

  const renderInputNodeList = (): ReactElement[] => {
    const menuItemList: ReactElement[] = []
    inputNodeData.forEach((nodeData) => {
      const filePath = nodeData.filePath
      if (Array.isArray(filePath)) {
        filePath.forEach((pathElm, index) => {
          menuItemList.push(
            <ListItem key={`${nodeData.nodeId}-${index}`}>
              <ListItemText
                primary={getFileName(pathElm)}
                secondary={pathElm}
              />
            </ListItem>,
          )
        })
      } else {
        menuItemList.push(
          <ListItem key={nodeData.nodeId}>
            <ListItemText
              primary={nodeData.nodeName}
              secondary={filePath || "No file path"}
            />
          </ListItem>,
        )
      }
    })

    return menuItemList
  }

  return (
    <BaseNodesView
      open={open}
      workspaceId={workspaceId}
      uid={uid}
      handleClose={handleClose}
      title="Input Node Data"
      data={inputNodeData}
      renderData={renderInputNodeList}
      emptyMessage="No input node data available"
    />
  )
}

export default InputsView
