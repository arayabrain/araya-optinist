import asyncio
import os
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
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

from studio.app.common.core.cloud.storage_tracking import (
    update_user_storage_after_workflow,
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

    Args:
        workspace_id: Workspace ID
        unique_id: Unique ID for the workflow
        params: Snakemake parameters
        user_id: User ID (for tracking free tier workflow counts)
    """
    client_id = get_client_id_for_subprocess()

    try:
        logger.info("Starting local execution mode")
        try:
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
        except BrokenProcessPool as e:
            # Phase A (#643): the worker process was terminated mid-run before
            # it could return a result -- most commonly an OOM-kill once the
            # workflow exhausted the container memory cap (+ bounded swap).
            # Because the worker died, the failure-handling tail inside
            # _snakemake_execute_process never ran, so without this the run
            # would stay "running" until WorkflowMonitor's ~2h timeout.
            # Surface the failure here so it appears immediately and in
            # isolation (only this run fails; the API stays up).
            logger.error(
                "snakemake worker process terminated unexpectedly "
                f"(likely out-of-memory): {e}"
            )
            _surface_terminated_workflow(workspace_id, unique_id)
            return False

        # Update user storage after workflow completion
        asyncio.run(update_user_storage_after_workflow(workspace_id))

        return future_result

    finally:
        # Decrement workflow count in finally block to ensure it ALWAYS runs
        # This prevents workflow count leaks when exceptions occur during execution
        if user_id is not None:
            try:
                from studio.app.common.core.workflow.workflow_tracking import (
                    decrement_workflow_count,
                )

                decrement_workflow_count(user_id)
                logger.info(f"Decremented workflow count for user {user_id}")
            except Exception as e:
                logger.error(f"Failed to decrement workflow count: {e}", exc_info=True)


def _surface_terminated_workflow(workspace_id: str, unique_id: str) -> None:
    """
    Record a workflow failure from the parent process when the execution worker
    was killed (e.g. OOM) and could not record its own failure.

    Mirrors the failure-handling tail of `_snakemake_execute_process` so the run
    transitions out of "running" immediately:
      1. Write a terminal error to the workflow error log, so that
         WorkflowResult.observe() reports the run as errored on the next poll.
      2. Release the remote-sync lock so observe_overall() can read remote
         storage (matches the failed-run branch in the worker).
      3. Run observe_overall() now to flip the experiment/node status to error.
      4. Force the remote sync status file into an error state.

    Every step is best-effort and isolated so one failure cannot mask another.
    """
    error_message = (
        "Workflow execution was terminated unexpectedly, most likely because it "
        "exceeded the available memory (out-of-memory). Try reducing the input "
        "data size or splitting the workflow into smaller steps."
    )

    # 1. Record the error so observe() reports has_error=True.
    try:
        SmkStatusLogger.record_external_error(workspace_id, unique_id, error_message)
    except Exception as e:
        logger.error(
            f"Failed to record terminated-workflow error: {e}", exc_info=True
        )

    # 2. Release the sync lock (mirrors the failed-run branch in the worker).
    if RemoteStorageController.is_available():
        try:
            RemoteSyncLockFileUtil.delete_sync_lock_file(workspace_id, unique_id)
        except Exception as e:
            logger.error(f"Failed to delete sync lock file: {e}", exc_info=True)

    # 3. Update workflow/experiment status now so the run is not stuck "running".
    try:
        asyncio.run(WorkflowResult(workspace_id, unique_id).observe_overall())
    except Exception as e:
        logger.error(
            f"Failed to update workflow result after termination: {e}",
            exc_info=True,
        )

    # 4. Force the remote sync status file into an error state.
    if RemoteStorageController.is_available():
        try:
            remote_bucket_name = RemoteSyncStatusFileUtil.get_remote_bucket_name(
                workspace_id, unique_id
            )
            RemoteSyncStatusFileUtil.create_sync_status_file_for_error(
                remote_bucket_name,
                workspace_id,
                unique_id,
                RemoteSyncAction.UPLOAD,
            )
        except Exception as e:
            logger.error(
                f"Failed to write error sync status file: {e}", exc_info=True
            )


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

    # --- Phase A (#643): protect the API from this workflow's resource use.
    # This child process -- and the snakemake / suite2p processes it spawns,
    # which inherit these settings -- is made the first OOM-killer victim and
    # de-prioritized, so a memory spike kills the workflow in isolation rather
    # than uvicorn/the API. The idle-class IO priority is what lets the bounded
    # container swap (see compute.tf linuxParameters) be used as a completion
    # cushion for borderline-large runs without its swap I/O starving the API
    # event loop and stalling /health.
    try:
        with open("/proc/self/oom_score_adj", "w") as _oom:
            _oom.write("800")  # range -1000..1000; higher = killed first
    except OSError:
        pass
    try:
        os.nice(10)  # lower CPU priority -- keep the API event loop responsive
    except OSError:
        pass
    try:
        import psutil

        psutil.Process().ionice(psutil.IOPRIO_CLASS_IDLE)  # lowest IO priority
    except Exception:
        pass
    # --- end Phase A ---

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

        logger.debug("Workflow API created successfully")
        logger.debug("Creating DAG...")

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

    # If workflow failed, delete the lock file BEFORE post-process
    # so that WorkflowResult.observe_overall() can access remote storage
    if not snakemake_result and RemoteStorageController.is_available():
        RemoteSyncLockFileUtil.delete_sync_lock_file(workspace_id, unique_id)

    # Wait for post_process upload to release the lock
    if snakemake_result and RemoteStorageController.is_available():
        RemoteSyncLockFileUtil.wait_for_lock_release(workspace_id, unique_id)

    try:
        # Update workflow processing results
        observe_success = False
        try:
            asyncio.run(WorkflowResult(workspace_id, unique_id).observe_overall())
            observe_success = True
        except Exception as e:
            logger.error(
                f"snakemake_execute post process (WorkflowResult) failed: {e}",
                exc_info=True,
            )

        # Update experiment database record if observe_overall() succeeded
        if observe_success and ExperimentRecordService.is_available():
            ExperimentRecordService.regist_record_on_workflow_completed(
                workspace_id, unique_id
            )

        # Data usage calculation
        if observe_success:
            WorkspaceDataCapacityService.update_experiment_data_usage(
                workspace_id, unique_id
            )

        if not observe_success:
            logger.warning(
                "Skipped experiment record registration and data usage update "
                "due to observe_overall() failure. [workspace: %s] [unique_id: %s]",
                workspace_id,
                unique_id,
            )
    except Exception as e:
        logger.error(f"snakemake_execute post process failed: {e}", exc_info=True)

    # result error handling
    if not snakemake_result:
        # Operate remote storage.
        if RemoteStorageController.is_available():
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
