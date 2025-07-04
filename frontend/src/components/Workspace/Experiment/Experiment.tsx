import { memo } from "react"
import { useSelector } from "react-redux"

import Loading from "components/common/Loading"
import { ExperimentTable } from "components/Workspace/Experiment/ExperimentTable"
import { CONTENT_HEIGHT } from "const/Layout"
import { selectLoading } from "store/slice/Experiments/ExperimentsSelectors"
import { selectImportSampleDataLoading } from "store/slice/FilesTree/FilesTreeSelectors"
import { selectLoading as selectFlowElementLoading } from "store/slice/FlowElement/FlowElementSelectors"

const Experiment = memo(function Experiment() {
  const loading = useSelector(selectLoading)
  const flowElementLoading = useSelector(selectFlowElementLoading)
  const importSampleDataLoading = useSelector(selectImportSampleDataLoading)

  return (
    <div style={{ display: "flex" }}>
      <main
        style={{
          display: "flex",
          flexDirection: "column",
          flexGrow: 1,
          minHeight: CONTENT_HEIGHT,
        }}
      >
        <ExperimentTable />
        <Loading
          loading={loading || flowElementLoading || importSampleDataLoading}
        />
      </main>
    </div>
  )
})

export default Experiment
