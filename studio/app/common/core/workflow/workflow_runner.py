import time
import uuid
from dataclasses import asdict
from typing import Dict, List, Optional

from studio.app.common.core.cloud.cloud_utils import get_user_subscription_plan
from studio.app.common.core.experiment.experiment_writer import ExptConfigWriter
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.rules.runner import Runner
from studio.app.common.core.snakemake.smk import FlowConfig, ForceRun, Rule, SmkParam
from studio.app.common.core.snakemake.snakemake_executor import (
    delete_dependencies,
    delete_procs_dependencies,
    snakemake_execute,
)
from studio.app.common.core.snakemake.snakemake_reader import SmkParamReader
from studio.app.common.core.snakemake.snakemake_rule import SmkRule
from studio.app.common.core.snakemake.snakemake_writer import SmkConfigWriter
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    RemoteSyncAction,
    RemoteSyncLockFileUtil,
    RemoteSyncStatusFileUtil,
)
from studio.app.common.core.workflow.workflow import (
    Node,
    NodeData,
    NodeType,
    NodeTypeUtil,
    ProcessType,
    RunItem,
)
from studio.app.common.core.workflow.workflow_params import get_typecheck_params
from studio.app.common.core.workflow.workflow_writer import WorkflowConfigWriter


class WorkflowRunner:
    def __init__(
        self,
        remote_bucket_name: str,
        workspace_id: str,
        unique_id: str,
        runItem: RunItem,
        user_id: Optional[int] = None,
    ) -> None:
        self.remote_bucket_name = remote_bucket_name
        self.workspace_id = workspace_id
        self.unique_id = unique_id
        self.runItem = runItem
        self.user_id = user_id
        self.nodeDict = self.runItem.nodeDict
        self.edgeDict = self.runItem.edgeDict
        self.logger = AppLogger.get_logger()

        # Log workflow start with timing
        self.workflow_start_time = time.time()
        self.logger.info(
            f"WORKFLOW START: {self.runItem.name} "
            f"(ID: {self.unique_id}, User: {self.user_id}, "
            f"Workspace: {self.workspace_id}) "
            f"at {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        WorkflowConfigWriter(
            self.workspace_id,
            self.unique_id,
            self.nodeDict,
            self.edgeDict,
        ).write()

        ExptConfigWriter(
            self.workspace_id,
            self.unique_id,
            self.runItem.name,
            nwbfile=get_typecheck_params(self.runItem.nwbParam, "nwb"),
            snakemake=get_typecheck_params(self.runItem.snakemakeParam, "snakemake"),
        ).write()

        Runner.clear_pid_file(self.workspace_id, self.unique_id)

    def log_workflow_completion(self, status: str = "completed"):
        """Log workflow completion with timing information."""
        if hasattr(self, "workflow_start_time"):
            end_time = time.time()
            duration = end_time - self.workflow_start_time
            self.logger.info(
                f"WORKFLOW {status.upper()}: {self.runItem.name} "
                f"(ID: {self.unique_id}, User: {self.user_id}) "
                f"completed in {duration:.2f}s at {time.strftime('%Y-%m-%d %H:%M:%S')}"
            )
        else:
            self.logger.info(
                f"WORKFLOW {status.upper()}: {self.runItem.name} "
                f"(ID: {self.unique_id}, User: {self.user_id}) "
                f"at {time.strftime('%Y-%m-%d %H:%M:%S')}"
            )

    @staticmethod
    def create_workflow_unique_id() -> str:
        new_unique_id = str(uuid.uuid4())[:8]
        return new_unique_id

    def run_workflow(self, background_tasks):
        # Operate remote storage data.
        if RemoteStorageController.is_available():
            # Check for remote-sync-lock-file
            # - If lock file exists, an exception is raised (raise_error=True)
            RemoteSyncLockFileUtil.check_sync_lock_file(
                self.workspace_id, self.unique_id, raise_error=True
            )

        self.set_smk_config()

        snakemake_params: SmkParam = get_typecheck_params(
            self.runItem.snakemakeParam, "snakemake"
        )
        snakemake_params = SmkParamReader.read(snakemake_params)
        snakemake_params.forcerun = self.runItem.forceRunList

        # delete dependencies for nodes
        if len(snakemake_params.forcerun) > 0:
            delete_dependencies(
                workspace_id=self.workspace_id,
                unique_id=self.unique_id,
                smk_params=snakemake_params,
                nodeDict=self.nodeDict,
                edgeDict=self.edgeDict,
            )

        # delete dependencies for procs
        delete_procs_dependencies(
            workspace_id=self.workspace_id,
            unique_id=self.unique_id,
            forceRunList=[
                ForceRun(
                    nodeId=ProcessType.POST_PROCESS.id,
                    name=ProcessType.POST_PROCESS.label,
                )
            ],
        )

        # Operate remote storage data.
        if RemoteStorageController.is_available():
            # creating remote-sync-lock-file
            RemoteSyncLockFileUtil.create_sync_lock_file(
                self.workspace_id, self.unique_id
            )

            # creating remote_sync_status file.
            # - The status file is used to pass bucket info to subsequent processing.
            RemoteSyncStatusFileUtil.create_sync_status_file_for_processing(
                self.remote_bucket_name,
                self.workspace_id,
                self.unique_id,
                RemoteSyncAction.UPLOAD,
            )

        background_tasks.add_task(
            snakemake_execute, self.workspace_id, self.unique_id, snakemake_params
        )

    def _calculate_priority(self) -> int:
        """Calculate priority based on user subscription tier."""
        snakemake_priority = 1  # Default for free users
        user_tier_info = None

        if self.user_id:
            try:
                # Use async function properly
                import asyncio

                # Get user subscription plan using the established async-in-sync pattern
                async def _get_user_subscription_async():
                    return await get_user_subscription_plan(self.user_id)

                try:
                    # Try to get existing event loop
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Create a new event loop in a thread for sync context
                        import concurrent.futures

                        with concurrent.futures.ThreadPoolExecutor() as executor:
                            future = executor.submit(
                                asyncio.run, _get_user_subscription_async()
                            )
                            user_tier_info = future.result()
                    else:
                        user_tier_info = loop.run_until_complete(
                            _get_user_subscription_async()
                        )
                except RuntimeError:
                    # No event loop, create new one
                    user_tier_info = asyncio.run(_get_user_subscription_async())
                tier = user_tier_info.get("tier", "free")
                is_premium = user_tier_info.get("is_premium", False)

                snakemake_priority = 10 if is_premium else 1

                self.logger.info(
                    f"PRIORITY ASSIGNMENT: Workflow {self.unique_id} "
                    f"(User: {self.user_id}) - Tier: {tier}, Priority: "
                    f"{snakemake_priority}, "
                    f"Premium: {is_premium}, Plan: "
                    f"{user_tier_info.get('plan_name', 'Unknown')}"
                )
            except Exception as e:
                self.logger.warning(
                    f"Failed to get user subscription tier for "
                    f"user {self.user_id}: {e}. "
                    f"Using default priority: {snakemake_priority}"
                )
        else:
            self.logger.info(
                f"PRIORITY ASSIGNMENT: Workflow {self.unique_id} - "
                f"No user_id provided, using default priority: {snakemake_priority}"
            )

        return snakemake_priority

    def set_smk_config(self):
        # Calculate priority based on user subscription tier first
        snakemake_priority = self._calculate_priority()

        rules, last_output = self.rulefile(snakemake_priority)

        nwb_template = get_typecheck_params(self.runItem.nwbParam, "nwb")

        flow_config = FlowConfig(
            rules=rules,
            last_output=last_output,
            nwb_template=nwb_template,
            snakemake_priority=snakemake_priority,
        )

        SmkConfigWriter.write_raw(
            self.workspace_id, self.unique_id, asdict(flow_config)
        )

    def rulefile(self, snakemake_priority: int = 1) -> Dict[str, Rule]:
        endNodeList = self.get_endNodeList()

        nwbfile = get_typecheck_params(self.runItem.nwbParam, "nwb")

        rule_dict: Dict[str, Rule] = {}
        last_outputs = []

        # generate a rule for each node
        for node in self.nodeDict.values():
            if NodeTypeUtil.check_nodetype(node.type) == NodeType.DATA:
                data_common_rule = SmkRule(
                    workspace_id=self.workspace_id,
                    unique_id=self.unique_id,
                    node=node,
                    edgeDict=self.edgeDict,
                    nwbfile=nwbfile,
                    priority=snakemake_priority,
                )
                data_rule = None

                if node.type == NodeType.IMAGE:
                    data_rule = data_common_rule.image()
                elif node.type == NodeType.CSV:
                    data_rule = data_common_rule.csv()
                elif node.type == NodeType.FLUO:
                    data_rule = data_common_rule.csv()
                elif node.type == NodeType.BEHAVIOR:
                    data_rule = data_common_rule.csv(nodeType="behavior")
                elif node.type == NodeType.HDF5:
                    data_rule = data_common_rule.hdf5()
                elif node.type == NodeType.MATLAB:
                    data_rule = data_common_rule.mat()
                elif node.type == NodeType.MICROSCOPE:
                    data_rule = data_common_rule.microscope()

                rule_dict[node.id] = data_rule

            elif NodeTypeUtil.check_nodetype(node.type) == NodeType.ALGO:
                algo_rule = SmkRule(
                    workspace_id=self.workspace_id,
                    unique_id=self.unique_id,
                    node=node,
                    edgeDict=self.edgeDict,
                    priority=snakemake_priority,
                ).algo(nodeDict=self.nodeDict)

                rule_dict[node.id] = algo_rule

                if node.id in endNodeList:
                    last_outputs.append(algo_rule.output)
            else:
                assert False, f"NodeType doesn't exists: {node.type}"

        # generate a rule for implicit post-process
        post_process_rule = SmkRule(
            workspace_id=self.workspace_id,
            unique_id=self.unique_id,
            node=Node(
                id=ProcessType.POST_PROCESS.id,
                type=ProcessType.POST_PROCESS.type,
                data=NodeData(
                    label=ProcessType.POST_PROCESS.label,
                    param=None,
                    path=last_outputs,
                    type=None,
                ),
                position=None,
                style=None,
            ),
            edgeDict={},
            priority=snakemake_priority,
        ).post_process()
        rule_dict[ProcessType.POST_PROCESS.type] = post_process_rule
        last_outputs.append(post_process_rule.output)

        return rule_dict, last_outputs

    def get_endNodeList(self) -> List[str]:
        returnCntDict = {key: 0 for key in self.nodeDict.keys()}
        for edge in self.edgeDict.values():
            returnCntDict[edge.source] += 1

        endNodeList = []
        for key, value in returnCntDict.items():
            if value == 0:
                endNodeList.append(key)
        return endNodeList
