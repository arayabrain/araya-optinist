import os
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import BackgroundTasks

from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
from studio.app.common.core.experiment.experiment_record_services import (
    ExperimentRecordService,
)
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
    RemoteStorageSimpleReader,
    RemoteStorageSimpleWriter,
    RemoteSyncAction,
    RemoteSyncLockFileUtil,
    RemoteSyncStatusFileUtil,
)
from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.common.core.workflow.workflow import (
    Node,
    NodeData,
    NodeType,
    NodeTypeUtil,
    OutputPath,
    OutputType,
    ProcessType,
    RunItem,
    WorkflowRunStatus,
)
from studio.app.common.core.workflow.workflow_params import get_typecheck_params
from studio.app.common.core.workflow.workflow_writer import WorkflowConfigWriter
from studio.app.const import (
    ACCEPT_FILE_EXT,
    DATE_FORMAT,
    METADATA_HDF5_STRUCTURE_FILE,
    METADATA_IMAGE_SHAPE_FILE,
    METADATA_MAT_STRUCTURE_FILE,
)
from studio.app.dir_path import DIRPATH


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

        # Track workflow start for free tier users (for load balancing)
        from studio.app.common.core.workflow.workflow_tracking import (
            increment_workflow_count,
        )

        increment_workflow_count(self.user_id)

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

    def _extract_input_files(self) -> List[str]:
        """Extract input file paths from workflow nodes."""
        input_files = []
        data_node_types = {
            NodeType.IMAGE,
            NodeType.CSV,
            NodeType.FLUO,
            NodeType.BEHAVIOR,
            NodeType.HDF5,
            NodeType.MATLAB,
            NodeType.MICROSCOPE,
        }
        for node in self.nodeDict.values():
            if (
                node.type in data_node_types
                or NodeTypeUtil.check_nodetype(node.type) == NodeType.DATA
            ):
                if node.data and node.data.path:
                    # path can be a string or list of strings
                    paths = (
                        node.data.path
                        if isinstance(node.data.path, list)
                        else [node.data.path]
                    )
                    input_files.extend(paths)
        return input_files

    async def _ensure_input_data_local(self) -> None:
        """Download any remote-only input files before workflow runs.

        Also updates and uploads metadata (image shape, HDF5/MATLAB structure)
        for downloaded files, helping to backfill metadata for files uploaded
        before structure caching was implemented.
        """
        if not RemoteStorageController.is_available():
            return

        input_files = self._extract_input_files()
        if not input_files:
            return

        # Track which metadata files need uploading
        metadata_to_upload = set()

        async with RemoteStorageSimpleReader(
            self.remote_bucket_name
        ) as remote_storage_controller:
            for filename in input_files:
                local_path = join_filepath(
                    [DIRPATH.INPUT_DIR, self.workspace_id, filename]
                )
                if not os.path.exists(local_path):
                    self.logger.info(f"Downloading input file from S3: {filename}")
                    try:
                        await remote_storage_controller.download_input_data(
                            self.workspace_id, filename
                        )
                        # Track metadata to update after download
                        metadata_file = self._get_metadata_file_for(filename)
                        if metadata_file:
                            metadata_to_upload.add((filename, metadata_file))
                    except Exception as e:
                        self.logger.error(
                            f"Failed to download input file {filename}: {e}"
                        )
                        raise

        # Update and upload metadata for downloaded files (in background)
        if metadata_to_upload:
            await self._update_and_upload_metadata(metadata_to_upload)

    def _get_metadata_file_for(self, filename: str) -> Optional[str]:
        """Get the metadata file name for a given input file type."""
        if filename.endswith(tuple(ACCEPT_FILE_EXT.TIFF_EXT.value)):
            return METADATA_IMAGE_SHAPE_FILE
        elif filename.endswith(tuple(ACCEPT_FILE_EXT.HDF5_EXT.value)):
            return METADATA_HDF5_STRUCTURE_FILE
        elif filename.endswith(tuple(ACCEPT_FILE_EXT.MATLAB_EXT.value)):
            return METADATA_MAT_STRUCTURE_FILE
        return None

    async def _update_and_upload_metadata(self, metadata_to_upload: set) -> None:
        """Update metadata caches and upload to S3.

        This helps backfill metadata for files uploaded before caching was added.
        """
        from studio.app.common.routers.files import (
            update_hdf5_structure,
            update_image_shape,
            update_mat_structure,
        )

        # Group by metadata file type
        metadata_files_updated = set()

        for filename, metadata_file in metadata_to_upload:
            try:
                if metadata_file == METADATA_IMAGE_SHAPE_FILE:
                    update_image_shape(self.workspace_id, filename)
                elif metadata_file == METADATA_HDF5_STRUCTURE_FILE:
                    update_hdf5_structure(self.workspace_id, filename)
                elif metadata_file == METADATA_MAT_STRUCTURE_FILE:
                    update_mat_structure(self.workspace_id, filename)
                metadata_files_updated.add(metadata_file)
                self.logger.debug(f"Updated metadata for {filename}")
            except Exception as e:
                self.logger.warning(f"Failed to update metadata for {filename}: {e}")

        # Upload updated metadata files to S3
        if metadata_files_updated:
            try:
                async with RemoteStorageSimpleWriter(
                    self.remote_bucket_name
                ) as remote_storage_controller:
                    for metadata_file in metadata_files_updated:
                        await remote_storage_controller.upload_input_data(
                            self.workspace_id, metadata_file
                        )
                        self.logger.debug(f"Uploaded {metadata_file} to S3")
            except Exception as e:
                self.logger.warning(f"Failed to upload metadata to S3: {e}")

    async def ensure_input_data_local(self) -> None:
        """Download any remote-only input files before workflow runs.

        Public method to be called from async context (e.g., router endpoints).
        """
        await self._ensure_input_data_local()

    def run_workflow(self, background_tasks: BackgroundTasks):
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
            snakemake_execute,
            self.workspace_id,
            self.unique_id,
            snakemake_params,
            self.user_id,
        )

    def finish_workflow_without_run(
        self, status: WorkflowRunStatus = WorkflowRunStatus.SUCCESS
    ):
        """
        Saves the settings and finishes the workflow without actually running it.
        - Function solely for creating experiment record.
        """

        # Load current configs
        expt_config = ExptConfigReader.read(self.workspace_id, self.unique_id)

        # Construct update data (ExptConfig.*)
        update_expt_config = ExptConfigReader.create_empty_experiment_config()
        now = datetime.now().strftime(DATE_FORMAT)
        update_expt_config.success = status.value
        update_expt_config.finished_at = now
        update_expt_config.data_usage = 0

        # Construct update data (ExptConfig.function)
        update_expt_config.function = {}
        for node_id, function in expt_config.function.items():
            function.success = WorkflowRunStatus.SUCCESS.value
            function.outputPaths = {
                "empty": OutputPath(
                    path="empty",
                    type=OutputType.EMPTY,
                    max_index=1,
                )
            }

            update_expt_config.function[node_id] = function

        # Prepare data (dict variable) for overwriting the config file
        update_expt_config_dict = {
            k: v for k, v in asdict(update_expt_config).items() if v is not None
        }

        # Overwrite config file
        ExptConfigWriter(self.workspace_id, self.unique_id).overwrite(
            update_expt_config_dict
        )

        # Update experiment database record
        if ExperimentRecordService.is_available():
            ExperimentRecordService.regist_record_on_workflow_completed(
                self.workspace_id, self.unique_id
            )

    def set_smk_config(self):
        rules, last_output = self.rulefile()

        nwb_template = get_typecheck_params(self.runItem.nwbParam, "nwb")

        # Get client_id from current logging context to pass to snakemake workflow
        from studio.app.common.core.logger_context_helpers import (
            get_client_id_for_subprocess,
        )

        client_id = get_client_id_for_subprocess()

        flow_config = FlowConfig(
            rules=rules,
            last_output=last_output,
            nwb_template=nwb_template,
            client_id=client_id,
        )

        SmkConfigWriter.write_raw(
            self.workspace_id, self.unique_id, asdict(flow_config)
        )

    def rulefile(self) -> Dict[str, Rule]:
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
