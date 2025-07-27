import { ReactElement } from "react"
import { useSelector } from "react-redux"

import { Grid } from "@mui/material"

import BaseNodesView, {
  renderVisualizationItems,
  useVisualizationCleanup,
  VisualizationItemData,
} from "components/Dataview/BaseNodesView"
import { selectNodeLabelById } from "store/slice/FlowElement/FlowElementSelectors"
import { getFileName } from "store/slice/FlowElement/FlowElementUtils"
import { selectInputNode } from "store/slice/InputNode/InputNodeSelectors"
import { RootState } from "store/store"
import { toDataTypeFromFileType } from "utils/DataTypeUtils"

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
  useVisualizationCleanup(open)

  const visualizationItems = useSelector(
    (state: RootState): VisualizationItemData[] => {
      const inputNodes = selectInputNode(state)
      const items: VisualizationItemData[] = []

      Object.entries(inputNodes)
        .filter(([, inputNode]) => inputNode.selectedFilePath != null)
        .forEach(([nodeId, inputNode]) => {
          const nodeName = selectNodeLabelById(nodeId)(state) || nodeId
          const dataType = toDataTypeFromFileType(inputNode.fileType)

          if (Array.isArray(inputNode.selectedFilePath)) {
            inputNode.selectedFilePath.forEach((filePath, index) => {
              items.push({
                nodeId,
                filePath,
                dataType,
                title: getFileName(filePath),
                subtitle: `Type: ${dataType}`,
                itemKey: `${nodeId}-${index}`,
              })
            })
          } else if (inputNode.selectedFilePath) {
            items.push({
              nodeId,
              filePath: inputNode.selectedFilePath,
              dataType,
              title: nodeName,
              subtitle: `Type: ${dataType}`,
              itemKey: nodeId,
            })
          }
        })

      return items
    },
  )

  const renderData = (): ReactElement[] => {
    if (visualizationItems.length === 0) {
      return []
    }

    return [
      <Grid container spacing={2} key="visualization-grid">
        {renderVisualizationItems(visualizationItems)}
      </Grid>,
    ]
  }

  return (
    <BaseNodesView
      open={open}
      workspaceId={workspaceId}
      uid={uid}
      handleClose={handleClose}
      title="Input Data"
      data={visualizationItems}
      renderData={renderData}
      emptyMessage="No input data available"
    />
  )
}

export default InputsView
