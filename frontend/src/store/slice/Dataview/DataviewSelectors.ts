import { createSelector } from "@reduxjs/toolkit"

import { VisualizationItemData } from "components/Dataview/BaseNodesView"
import { DATAVIEW_SLICE_NAME } from "store/slice/Dataview/DataviewType"
import { selectNodeLabelById } from "store/slice/FlowElement/FlowElementSelectors"
import { getFileName } from "store/slice/FlowElement/FlowElementUtils"
import { selectInputNode } from "store/slice/InputNode/InputNodeSelectors"
import { selectPipelineNodeResultSuccessList } from "store/slice/Pipeline/PipelineSelectors"
import { RootState } from "store/store"
import { toDataTypeFromFileType } from "utils/DataTypeUtils"

const selectDataviewSlice = (state: RootState) => state[DATAVIEW_SLICE_NAME]

export const selectDataviewPrivateData = createSelector(
  selectDataviewSlice,
  (dataview) => dataview.data.private,
)

export const selectDataviewPublicData = createSelector(
  selectDataviewSlice,
  (dataview) => dataview.data.public,
)

export const selectDataviewLoading = createSelector(
  selectDataviewSlice,
  (dataview) => dataview.loading,
)

export const selectInputVisualizationItems = createSelector(
  [selectInputNode, (state: RootState) => state],
  (inputNodes, state): VisualizationItemData[] => {
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

export const selectOutputVisualizationItems = (uid: string | undefined) =>
  createSelector(
    [selectPipelineNodeResultSuccessList, (state: RootState) => state],
    (runResult, state) => {
      if (uid != null) {
        try {
          return runResult.map(({ nodeId, nodeResult }) => {
            return {
              nodeId,
              nodeName: selectNodeLabelById(nodeId)(state) || nodeId,
              items: Object.entries(nodeResult.outputPaths).map(
                ([outputKey, value]) =>
                  ({
                    nodeId,
                    filePath: value.path,
                    dataType: value.type,
                    title: outputKey,
                    subtitle: `Type: ${value.type}`,
                    itemKey: `${nodeId}-${outputKey}`,
                  }) as VisualizationItemData,
              ),
            }
          })
        } catch (error) {
          // eslint-disable-next-line no-console
          console.warn("Error loading output data:", error)
          return []
        }
      } else {
        return []
      }
    },
  )
