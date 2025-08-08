import { ReactElement } from "react"
import { useSelector } from "react-redux"

import { Grid } from "@mui/material"

import BaseNodesView, {
  renderVisualizationItems,
  useDataviewVisualizationCleanup,
} from "components/Dataview/BaseNodesView"
import { selectInputVisualizationItems } from "store/slice/Dataview/DataviewSelectors"

type InputsViewProps = {
  open: boolean
  is_public: boolean
  workspaceId: number | undefined
  uid: string | undefined
  handleClose: () => void
}

const InputsView = ({
  open,
  is_public,
  workspaceId,
  uid,
  handleClose,
}: InputsViewProps) => {
  useDataviewVisualizationCleanup(open)

  const inputVisualizationItems = useSelector(selectInputVisualizationItems)

  const renderData = (): ReactElement[] => {
    if (inputVisualizationItems.length === 0) {
      return []
    }

    return [
      <Grid container spacing={2} key="visualization-grid">
        {renderVisualizationItems(inputVisualizationItems)}
      </Grid>,
    ]
  }

  return (
    <BaseNodesView
      open={open}
      is_public={is_public}
      workspaceId={workspaceId}
      uid={uid}
      handleClose={handleClose}
      title="Input Data"
      data={inputVisualizationItems}
      renderData={renderData}
      emptyMessage="No input data available"
    />
  )
}

export default InputsView
