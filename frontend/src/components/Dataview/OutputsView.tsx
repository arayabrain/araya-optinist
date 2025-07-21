import { ReactElement } from "react"
import { useSelector } from "react-redux"

import BaseNodesView, {
  Divider,
  ListSubheader,
  ListItem,
  ListItemText,
} from "components/Dataview/BaseNodesView"
import { selectNodeLabelById } from "store/slice/FlowElement/FlowElementSelectors"
import { selectPipelineNodeResultSuccessList } from "store/slice/Pipeline/PipelineSelectors"
import { RootState } from "store/store"

type OutputsViewProps = {
  open: boolean
  workspaceId: number | undefined
  uid: string | undefined
  handleClose: () => void
}

const OutputsView = ({
  open,
  workspaceId,
  uid,
  handleClose,
}: OutputsViewProps) => {
  const algorithmNodeOutputPathInfoList = useSelector((state: RootState) => {
    if (uid != null) {
      const runResult = selectPipelineNodeResultSuccessList(state)
      return runResult.map(({ nodeId, nodeResult }) => {
        return {
          nodeId,
          nodeName: selectNodeLabelById(nodeId)(state) || nodeId,
          paths: Object.entries(nodeResult.outputPaths).map(
            ([outputKey, value]) => {
              return {
                outputKey,
                filePath: value.path,
                type: value.type,
              }
            },
          ),
        }
      })
    } else {
      return []
    }
  })

  const renderAlgorithmNodeOutputList = (): ReactElement[] => {
    const menuItemList: ReactElement[] = []

    // Render Algorithm Node Output Data
    algorithmNodeOutputPathInfoList.forEach((pathInfo) => {
      menuItemList.push(
        <ListSubheader key={`algo-header-${pathInfo.nodeId}`}>
          <Divider textAlign="center">{pathInfo.nodeName}</Divider>
        </ListSubheader>,
      )
      pathInfo.paths.forEach((outputPath) => {
        menuItemList.push(
          <ListItem key={`${pathInfo.nodeId}-${outputPath.outputKey}`}>
            <ListItemText
              primary={`${outputPath.outputKey} (${outputPath.type})`}
              secondary={outputPath.filePath}
            />
          </ListItem>,
        )
      })
    })

    return menuItemList
  }

  return (
    <BaseNodesView
      open={open}
      workspaceId={workspaceId}
      uid={uid}
      handleClose={handleClose}
      title="Algorithm Node Outputs"
      data={algorithmNodeOutputPathInfoList}
      renderData={renderAlgorithmNodeOutputList}
      emptyMessage="No output data available"
    />
  )
}

export default OutputsView
