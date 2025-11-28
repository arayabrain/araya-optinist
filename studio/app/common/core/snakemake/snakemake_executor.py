import asyncio
import os
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Dict, List

from snakemake.api import (
    DAGSettings,
    DeploymentMethod,
    DeploymentSettings,
    OutputSettings,
    ResourceSettings,
    SnakemakeApi,
    StorageSettings,
)

from studio.app.common.core.cloud.cloud_utils import update_user_storage_after_workflow
from studio.app.common.core.cloud_batch.batch_config import BATCH_CONFIG
from studio.app.common.core.cloud_batch.batch_snakemake_executor import (
    BatchSnakemakeExecutor,
)
from studio.app.common.core.experiment.experiment_record_services import (
    ExperimentRecordService,
)
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.logger_context_helpers import (
    get_client_id_for_subprocess,
    with_client_id_context,
)
from studio.app.common.core.snakemake.smk import ForceRun, SmkParam
from studio.app.common.core.snakemake.smk_status_logger import SmkStatusLogger
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    RemoteSyncAction,
    RemoteSyncLockFileUtil,
    RemoteSyncStatusFileUtil,
)
from studio.app.common.core.utils.filepath_creater import get_pickle_file, join_filepath
from studio.app.common.core.workflow.workflow import Edge, Node
from studio.app.common.core.workflow.workflow_result import WorkflowResult
from studio.app.common.core.workspace.workspace_data_capacity_services import (
    WorkspaceDataCapacityService,
)
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()


def snakemake_execute(
    workspace_id: str, unique_id: str, params: SmkParam, user_id: int = None
):
    """
    Main entry point for Snakemake execution.
    Determines whether to use local or AWS Batch execution based on configuration.

    Args:
        workspace_id: Workspace ID
        unique_id: Unique ID for the workflow
        params: Snakemake parameters
        user_id: User ID (for tracking free tier workflow counts)
    """
    client_id = get_client_id_for_subprocess()

    if BATCH_CONFIG.USE_AWS_BATCH:
        # BATCH: This should ALWAYS appear if batch mode is enabled
        print(f"BATCH: USE_AWS_BATCH=True, client_id={client_id}", flush=True)
        logger.info("Starting AWS Batch execution mode")
        logger.debug("BATCH: If you see this, optinist logging works!")

        # Use BatchSnakemakeExecutor for all batch execution logic
        batch_executor = BatchSnakemakeExecutor(workspace_id, unique_id)
        future_result = asyncio.run(batch_executor.execute_batch_workflow(params))
    else:
        logger.info("Starting local execution mode")
        with ProcessPoolExecutor(max_workers=1) as executor:
            logger.info("start snakemake running process.")

            future = executor.submit(
                _snakemake_execute_process,
                workspace_id,
                unique_id,
                params,
                client_id=client_id,
            )
            future_result = future.result()

    # Update user storage after workflow completion
    asyncio.run(update_user_storage_after_workflow(workspace_id))

    # Decrement workflow count for free tier users (for load balancing)
    if user_id is not None:
        try:
            from studio.app.common.core.workflow.workflow_tracking import (
                decrement_workflow_count,
            )

            decrement_workflow_count(user_id)
        except Exception as e:
            logger.error(f"Failed to decrement workflow count: {e}")

    return future_result


@with_client_id_context  # Automatically set client_id for logging
def _snakemake_execute_process(
    workspace_id: str,
    unique_id: str,
    params: SmkParam,
    client_id: str = None,
) -> bool:
    # ------------------------------------------------------------
    # Snakemake execution process
    # ------------------------------------------------------------

    smk_logger = SmkStatusLogger(workspace_id, unique_id)
    smk_workdir = join_filepath(
        [
            DIRPATH.OUTPUT_DIR,
            workspace_id,
            unique_id,
        ]
    )

    # Use context manager for proper cleanup
    cores = getattr(params, "cores", 1)

    deployment_methods = []
    if getattr(params, "use_conda", True):
        deployment_methods.append(DeploymentMethod.CONDA)

    # Use context manager for proper cleanup
    with SnakemakeApi(
        OutputSettings(
            verbose=True,  # Print debugging output
            show_failed_logs=True,  # Automatically display logs of failed jobs
            debug_dag=True,  # Print candidate and selected jobs with wildcards
            printshellcmds=True,  # Show shell commands
        ),
    ) as snakemake_api:
        workflow_api = snakemake_api.workflow(
            snakefile=Path(DIRPATH.SNAKEMAKE_FILEPATH),
            workdir=Path(smk_workdir),
            storage_settings=StorageSettings(),
            resource_settings=ResourceSettings(cores=cores),
            deployment_settings=DeploymentSettings(
                deployment_method=deployment_methods,
                conda_frontend="conda",
                conda_prefix=DIRPATH.SNAKEMAKE_CONDA_ENV_DIR,
            ),
        )

        logger.info("Workflow API created successfully")
        logger.info("Creating DAG...")

        forceall = getattr(params, "forceall", False)

        dag_api = workflow_api.dag(
            dag_settings=DAGSettings(
                forceall=forceall,
            )
        )

        logger.info("DAG created successfully")
        logger.info("Starting workflow execution...")

        snakemake_result = False

        try:
            dag_api.execute_workflow()

            snakemake_result = True
            logger.info("snakemake_execute succeeded.")
        except Exception as e:
            snakemake_result = False
            logger.error(f"snakemake_execute failed: {e}")

            # Logging errors via SmkStatusLogger to notify
            #   the monitoring process (WorkflowMonitor) of the error occurrence
            smk_logger.logger.error(e)

    if snakemake_result:
        logger.info("snakemake_execute succeeded.")
    else:
        logger.error("snakemake_execute failed..")

    smk_logger.clean_up()

    # ------------------------------------------------------------
    # Snakemake execution post process
    # ------------------------------------------------------------

    try:
        # Update workflow processing results
        try:
            asyncio.run(WorkflowResult(workspace_id, unique_id).observe_overall())
        except Exception as e:
            logger.error(
                f"snakemake_execute post process (WorkflowResult) failed: {e}",
                exc_info=True,
            )

        # Update experiment database record
        if ExperimentRecordService.is_available():
            ExperimentRecordService.regist_record_on_workflow_completed(
                workspace_id, unique_id
            )

        # Data usage calculation
        WorkspaceDataCapacityService.update_experiment_data_usage(
            workspace_id, unique_id
        )
    except Exception as e:
        logger.error(f"snakemake_execute post process failed: {e}", exc_info=True)

    # result error handling
    if not snakemake_result:
        # Operate remote storage.
        if RemoteStorageController.is_available():
            # force delete sync lock file
            RemoteSyncLockFileUtil.delete_sync_lock_file(workspace_id, unique_id)

            remote_bucket_name = RemoteSyncStatusFileUtil.get_remote_bucket_name(
                workspace_id, unique_id
            )

            # force update sync status file
            RemoteSyncStatusFileUtil.create_sync_status_file_for_error(
                remote_bucket_name,
                workspace_id,
                unique_id,
                RemoteSyncAction.UPLOAD,
            )

    return snakemake_result


# NOTE: The old _snakemake_execute_batch() function (~580 lines) has been completely
# replaced by BatchSnakemakeExecutor and removed from this file.
# See:
# studio.app.common.core.cloud_batch.batch_snakemake_executor.BatchSnakemakeExecutor


def delete_dependencies(
    workspace_id: str,
    unique_id: str,
    smk_params: SmkParam,
    nodeDict: Dict[str, Node],
    edgeDict: Dict[str, Edge],
):
    queue = deque()

    for param in smk_params.forcerun:
        queue.append(param.nodeId)

    while True:
        # terminate condition
        if len(queue) == 0:
            break

        # delete pickle
        node_id = queue.pop()
        algo_name = nodeDict[node_id].data.label

        pickle_filepath = join_filepath(
            [
                DIRPATH.OUTPUT_DIR,
                get_pickle_file(
                    workspace_id=workspace_id,
                    unique_id=unique_id,
                    node_id=node_id,
                    algo_name=algo_name,
                ),
            ]
        )

        if os.path.exists(pickle_filepath):
            os.remove(pickle_filepath)

        # 全てのedgeを見て、node_idがsourceならtargetをqueueに追加する
        for edge in edgeDict.values():
            if node_id == edge.source:
                queue.append(edge.target)


def delete_procs_dependencies(
    workspace_id: str,
    unique_id: str,
    forceRunList: List[ForceRun],
):
    """
    Delete procs (ExptConfig.procs) dependencies
    """

    for proc in forceRunList:
        # delete pickle
        pickle_filepath = join_filepath(
            [
                DIRPATH.OUTPUT_DIR,
                get_pickle_file(
                    workspace_id=workspace_id,
                    unique_id=unique_id,
                    node_id=proc.nodeId,
                    algo_name=proc.name,
                ),
            ]
        )

        if os.path.exists(pickle_filepath):
            os.remove(pickle_filepath)
