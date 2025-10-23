import asyncio
import os
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Dict, List

from snakemake.api import (
    DAGSettings,
    DefaultResources,
    DeploymentMethod,
    DeploymentSettings,
    ExecutionSettings,
    OutputSettings,
    RemoteExecutionSettings,
    ResourceSettings,
    SnakemakeApi,
    StorageSettings,
)
from snakemake_executor_plugin_aws_batch import ExecutorSettings

from studio.app.common.core.cloud.cloud_utils import update_user_storage_after_workflow
from studio.app.common.core.cloud_batch.batch_config import BATCH_CONFIG
from studio.app.common.core.cloud_batch.batch_utils import (
    BatchDebug,
    BatchUtils,
    download_workflow_results_from_s3,
    upload_snakefile_to_s3,
    upload_snakemake_config_to_s3,
    upload_workflow_results_to_s3,
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


def snakemake_execute(workspace_id: str, unique_id: str, params: SmkParam):
    """
    Main entry point for Snakemake execution.
    Determines whether to use local or AWS Batch execution based on configuration.
    """
    client_id = get_client_id_for_subprocess()

    if BATCH_CONFIG.USE_AWS_BATCH:
        # DIAGNOSTIC: This should ALWAYS appear if batch mode is enabled
        print(f"DIAGNOSTIC: USE_AWS_BATCH=True, client_id={client_id}", flush=True)
        logger.info("Starting AWS Batch execution mode")
        logger.error("DIAGNOSTIC: If you see this, optinist logging works!")
        future_result = _snakemake_execute_batch(
            workspace_id, unique_id, params, client_id=client_id
        )
        # Handle S3 operations for batch execution
        # Upload/download ensures results are in S3 and synced to local storage
        if future_result:
            # Upload final workflow results to S3 for persistence
            asyncio.run(upload_workflow_results_to_s3(workspace_id, unique_id))
            # Download results from S3 to local storage for post-processing
            asyncio.run(download_workflow_results_from_s3(workspace_id, unique_id))
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


@with_client_id_context  # Automatically set client_id for logging
def _snakemake_execute_batch(
    workspace_id: str, unique_id: str, params: SmkParam, client_id: str = None
) -> bool:
    """
    Execute Snakemake workflow using AWS Batch executor.
    """

    smk_logger = SmkStatusLogger(workspace_id, unique_id)

    # Configure workdir based on storage mode
    # The workdir is used for .snakemake metadata and as base for config/Snakefile paths
    if RemoteStorageController.is_available():
        # S3 mode: use /app as workdir so all source files (scripts at
        # /app/studio/app/...) are within workdir and can be bundled by Snakemake
        # for remote batch execution. Config downloaded to /app/snakemake.yaml.
        smk_workdir = "/app"
    else:
        # EFS mode: use absolute path
        smk_workdir = (
            Path(
                join_filepath(
                    [
                        DIRPATH.OUTPUT_DIR,
                        workspace_id,
                        unique_id,
                    ]
                )
            )
            .resolve()
            .as_posix()
        )

    try:
        # Initialize BatchExecutor for AWS Batch specific operations
        print(
            f"DIAGNOSTIC: Inside _snakemake_execute_batch "
            f"for {workspace_id}/{unique_id}",
            flush=True,
        )
        logger.info("Load BatchExecutor")
        logger.error("DIAGNOSTIC: Entered _snakemake_execute_batch successfully")
        print(
            f"DIAGNOSTIC: About to create BatchUtils({workspace_id}, {unique_id})...",
            flush=True,
        )
        batch_executor = BatchUtils(workspace_id, unique_id)
        print("DIAGNOSTIC: BatchUtils created successfully", flush=True)
        logger.info("DIAGNOSTIC: BatchUtils object created successfully")

        # Configure S3 bucket name EARLY for batch execution
        # This must happen before any Snakemake APIs capture environment state
        # to ensure it's available for batch job definitions created dynamically
        if RemoteStorageController.is_available():
            batch_s3_bucket = os.environ.get(
                "S3_DEFAULT_BUCKET_NAME",
                BATCH_CONFIG.AWS_BATCH_S3_BUCKET_NAME,
            )
            if not batch_s3_bucket:
                logger.error(
                    "AWS Batch S3 bucket not configured. "
                    "S3_DEFAULT_BUCKET_NAME environment variable is required."
                )
                return False

            # Set early to ensure it's available for all subsequent operations
            os.environ["AWS_BATCH_S3_BUCKET_NAME"] = batch_s3_bucket
            logger.info(f"Set AWS_BATCH_S3_BUCKET_NAME early: {batch_s3_bucket}")

        # Debug AWS Batch environment status for immediate visibility
        # BatchDebug.debug_batch_environment(batch_executor)
        # if not BatchDebug.validate_batch_configuration(batch_executor):
        #     return False

        # Upload config file to S3 so batch jobs can access it
        config_upload_success = asyncio.run(
            upload_snakemake_config_to_s3(workspace_id, unique_id)
        )
        if not config_upload_success:
            logger.error("Failed to upload snakemake config to S3")
            return False

        # Upload Snakefile to S3 so batch jobs can access it
        snakefile_upload_success = asyncio.run(
            upload_snakefile_to_s3(workspace_id, unique_id)
        )
        if not snakefile_upload_success:
            logger.error("Failed to upload Snakefile to S3")
            return False

        # Prepare workspace for batch execution
        logger.info("Prepare batch workspace")
        batch_executor.prepare_batch_workspace()

        # Create symlink to config file for DAG creation on main instance
        # When workdir is /app, Snakefile expects config at /app/snakemake.yaml
        config_source = join_filepath(
            [DIRPATH.OUTPUT_DIR, workspace_id, unique_id, DIRPATH.SNAKEMAKE_CONFIG_YML]
        )
        config_symlink = "/app/snakemake.yaml"

        # Remove existing symlink/file if present
        if os.path.islink(config_symlink):
            os.unlink(config_symlink)
        elif os.path.exists(config_symlink):
            os.remove(config_symlink)

        # Create symlink so Snakefile can find config during DAG creation
        os.symlink(config_source, config_symlink)
        logger.info(f"Created config symlink: {config_symlink} -> {config_source}")

        # Configure storage based on availability
        # Try S3 first with simplified config (relies on _make_relative_path() fix)
        # Falls back to EFS if S3 not available
        storage_settings = None
        if RemoteStorageController.is_available():
            # Use S3 when available
            s3_prefix = BATCH_CONFIG.AWS_DEFAULT_PROVIDER.lower()
            s3_bucket_name = os.environ.get(
                "S3_DEFAULT_BUCKET_NAME", BATCH_CONFIG.AWS_BATCH_S3_BUCKET_NAME
            )
            s3_storage = f"{s3_prefix}://{s3_bucket_name}/app/studio_data"

            storage_settings = StorageSettings(
                default_storage_provider="s3",  # Use S3 storage plugin
                default_storage_prefix=s3_storage,  # S3 prefix with bucket and path
                local_storage_prefix=Path(
                    ".snakemake/storage"
                ),  # Use Snakemake default
                remote_job_local_storage_prefix=Path(
                    ".snakemake/storage"
                ),  # Use Snakemake default
                shared_fs_usage=frozenset(["s3"]),
                retrieve_storage=True,
                keep_storage_local=False,
            )
            # logger.debug(
            #     f"S3 storage breakdown: provider='{s3_prefix}', "
            #     f"bucket='{s3_bucket_name}', full_prefix='{s3_storage}'"
            # )
            # logger.debug(f"DIRPATH.DATA_DIR: {DIRPATH.DATA_DIR}")
        else:
            # Use optimized EFS configuration when S3 is not available
            logger.debug("S3 not available, configuring optimized EFS storage")
            BatchUtils.prepare_efs_environment(workspace_id)
            storage_settings = BatchUtils.get_efs_optimized_storage_settings(
                workspace_id, unique_id
            )

        # Use context manager for proper cleanup
        with SnakemakeApi(
            OutputSettings(
                verbose=True,  # Print debugging output
                show_failed_logs=True,  # Automatically display logs of failed jobs
                debug_dag=True,  # Print candidate and selected jobs with wildcards
                printshellcmds=True,  # Show shell commands
            ),
        ) as snakemake_api:
            # Use the original Snakefile for DAG creation on the main container
            # The batch executor will handle passing the S3-uploaded
            # Snakefile to workers
            workflow_api = snakemake_api.workflow(
                snakefile=Path(DIRPATH.SNAKEMAKE_FILEPATH),
                workdir=Path(smk_workdir),
                storage_settings=storage_settings,
                resource_settings=ResourceSettings(
                    cores=1,  # Use 1 core for debugging
                    nodes=1,  # # Use 1 node for debugging
                    default_resources=DefaultResources(["mem_mb=4096"]),
                ),
                deployment_settings=DeploymentSettings(
                    deployment_method={DeploymentMethod.CONDA},
                    conda_frontend="conda",
                    conda_prefix=DIRPATH.SNAKEMAKE_CONDA_ENV_DIR,
                ),
            )

            logger.info("Workflow API created successfully for AWS Batch")
            logger.info("Creating DAG...")

            forceall = getattr(params, "forceall", False)

            dag_settings = DAGSettings(
                forceall=forceall,
            )
            logger.info("DAG settings created")

            try:
                dag_api = workflow_api.dag(
                    dag_settings=dag_settings,
                )
                logger.info("DAG created successfully")
            except Exception as e:
                logger.error(f"Failed to create DAG: {e}")
                raise e

            # Perform verbose dryrun validation before submitting expensive batch jobs
            logger.info(
                "Running verbose workflow dryrun validation before batch execution..."
            )

            # Determine storage mode for dryrun validation
            # storage_mode = "s3" if RemoteStorageController.is_available() else "efs"

            # Call BatchDebug to perform dryrun validation
            # BatchDebug.debug_dryrun_validation(dag_api, storage_mode)

            logger.info("Starting workflow execution on AWS Batch...")
            try:
                # Execute workflow - Snakemake will handle job submission to AWS Batch
                # Get user-appropriate job queue (free or paid plan)
                selected_job_queue = batch_executor.get_job_queue_for_user()
                logger.info(f"Using AWS Batch job queue: {selected_job_queue}")

                # Temporarily remove AWS credentials from environment for batch jobs
                # Forces batch jobs to use IAM roles instead of hardcoded credentials
                aws_access_key = os.environ.pop("AWS_ACCESS_KEY_ID", None)
                aws_secret_key = os.environ.pop("AWS_SECRET_ACCESS_KEY", None)

                try:
                    # Set IN_SNAKEMAKE_BATCH so batch containers can find config
                    os.environ["IN_SNAKEMAKE_BATCH"] = "true"

                    # Prepare environment variables for batch jobs
                    envvars = ["USE_AWS_BATCH", "OPTINIST_DIR", "IN_SNAKEMAKE_BATCH"]
                    if RemoteStorageController.is_available():
                        # Use S3 storage for batch jobs
                        # Set AWS_BATCH_S3_BUCKET_NAME from S3_DEFAULT_BUCKET_NAME
                        # Needed for download_snakemake_config_from_s3() and
                        # download_snakefile_from_s3() functions inside
                        # batch container. Jobs use AWS_BATCH_S3_BUCKET_NAME to
                        # know which bucket contains the config/Snakefile
                        batch_s3_bucket = os.environ.get(
                            "S3_DEFAULT_BUCKET_NAME",
                            BATCH_CONFIG.AWS_BATCH_S3_BUCKET_NAME,
                        )

                        if not batch_s3_bucket:
                            logger.error(
                                "AWS Batch S3 bucket not configured. "
                                "S3_DEFAULT_BUCKET_NAME environment "
                                "variable is required."
                            )
                            raise ValueError(
                                "S3_DEFAULT_BUCKET_NAME environment variable must "
                                "be set for AWS Batch execution with S3 storage"
                            )

                        # Set the variable for the main process and batch jobs
                        os.environ["AWS_BATCH_S3_BUCKET_NAME"] = batch_s3_bucket

                        logger.info(
                            f"Configured AWS Batch S3 bucket: {batch_s3_bucket}"
                        )

                        envvars.extend(
                            [
                                "REMOTE_STORAGE_TYPE",
                                "S3_DEFAULT_BUCKET_NAME",
                                "AWS_BATCH_S3_BUCKET_NAME",
                                "AWS_DEFAULT_REGION",
                                "PYTHONPATH",
                            ]
                        )
                        logger.info("Using S3 storage for batch jobs")
                    else:
                        # Set env variables for EFS storage before adding to envvars
                        os.environ["EFS_MOUNT_TARGET"] = DIRPATH.DATA_DIR
                        os.environ["TMPDIR"] = "/tmp"
                        os.environ["TMP"] = "/tmp"
                        envvars.extend(["EFS_MOUNT_TARGET", "TMPDIR", "TMP"])
                        logger.info("Using EFS storage for batch jobs")

                    # Prepare container setup commands based on storage mode
                    if RemoteStorageController.is_available():
                        # S3 mode: download config and Snakefile from S3
                        contain_setup = BatchUtils.get_s3_container_setup_commands(
                            workspace_id, unique_id
                        )
                        logger.info(
                            "Using S3 container setup (config/Snakefile download)"
                        )
                    else:
                        # EFS mode: full container setup with EFS mount and downloads
                        contain_setup = BatchUtils.get_container_setup_commands(
                            workspace_id, unique_id
                        )
                        logger.info("Using EFS container setup (full environment)")

                    # Debug container command configuration (uncomment to enable)
                    # BatchDebug.debug_container_command(batch_executor, contain_setup)

                    # Debug: Log the contain_setup to understand the structure
                    logger.info(f"Container setup commands: {contain_setup}")
                    logger.info(f"Container setup type: {type(contain_setup)}")
                    for i, cmd in enumerate(contain_setup):
                        logger.info(f"Command {i}: {repr(cmd)} (type: {type(cmd)})")

                    # Ensure all commands are strings and flatten any nested lists
                    if contain_setup:
                        flattened_commands = []
                        for cmd in contain_setup:
                            if isinstance(cmd, list):
                                flattened_commands.extend(str(subcmd) for subcmd in cmd)
                            else:
                                flattened_commands.append(str(cmd))
                        # Join commands with " && " as expected by Snakemake precommand
                        contain_setup = " && ".join(flattened_commands)
                        logger.info(f"Joined precommand: {contain_setup}")

                    # Debug AWS Batch execution (uncomment to enable)
                    # BatchDebug.debug_aws_batch_execution(
                    #     batch_executor, selected_job_queue, envvars, contain_setup
                    # )

                    # Store start time for monitoring
                    execution_start_time = time.time()
                    logger.info(f"Starting DAG execution at {execution_start_time}")

                    # Start job monitoring before execution
                    logger.info("Starting enhanced job monitoring...")
                    batch_executor.start_job_monitoring()

                    snakemake_result = False

                    try:
                        dag_api.execute_workflow(
                            executor="aws-batch",
                            execution_settings=ExecutionSettings(
                                retries=1,
                                keep_going=False,
                                latency_wait=300,
                            ),
                            executor_settings=ExecutorSettings(
                                region=BATCH_CONFIG.AWS_DEFAULT_REGION,
                                job_queue=selected_job_queue,
                                job_role=BATCH_CONFIG.AWS_BATCH_JOB_ROLE,
                                task_timeout=1800,  # Increase timeout to 30 minutes
                            ),
                            remote_execution_settings=RemoteExecutionSettings(
                                container_image=batch_executor.get_container_image(),
                                envvars=envvars,
                                jobname="optinist-{rulename}-{jobid}",
                                # Add container setup commands for EFS optimization
                                precommand=contain_setup if contain_setup else None,
                            ),
                        )

                        execution_duration = time.time() - execution_start_time
                        logger.info(
                            f"DAG execution completed in {execution_duration:.2f}s"
                        )

                    except Exception as exec_error:
                        execution_duration = time.time() - execution_start_time
                        logger.error(
                            f"DAG execution failed after {execution_duration:.2f}s"
                        )
                        logger.error(f"Execution error: {exec_error}")

                        # Try to get more detailed job information
                        try:
                            recent_failed = batch_executor.get_recent_failed_jobs(
                                limit=3, include_context=True
                            )
                            if recent_failed:
                                logger.error(
                                    f"Found {len(recent_failed)} recent failed jobs "
                                    "with detailed context"
                                )

                                for i, job_context in enumerate(recent_failed):
                                    job_id = job_context.get("job_id", "Unknown")
                                    logger.error(f"Failed job {i+1}: {job_id}")

                                    # Show quick failure summary
                                    exit_code = job_context.get("exit_code")
                                    exit_reason = job_context.get(
                                        "exit_reason", "Unknown"
                                    )
                                    logger.error(
                                        f"  Exit Code: {exit_code},  "
                                        f"Reason: {exit_reason}"
                                    )

                            try:
                                succeeded_jobs = batch_executor.batch_client.list_jobs(
                                    jobQueue=batch_executor.get_job_queue_for_user(),
                                    jobStatus="SUCCEEDED",
                                    maxResults=5,
                                )
                                if succeeded_jobs.get("jobList"):
                                    logger.info(
                                        f"Found {len(succeeded_jobs['jobList'])} "
                                        "recent SUCCEEDED jobs:"
                                    )
                                    for job in succeeded_jobs["jobList"][:3]:
                                        logger.info(
                                            f"  SUCCESS: {job['jobName']} "
                                            f"({job['jobId']})"
                                        )
                                else:
                                    logger.warning("No recent SUCCEEDED jobs found")
                            except Exception as e:
                                logger.warning(f"Could not check succeeded jobs: {e}")

                            # Show failure analysis summary for each failed job
                            for job_context in recent_failed:
                                failure_analysis = job_context.get(
                                    "failure_analysis", {}
                                )
                                if failure_analysis.get("likely_causes"):
                                    logger.error(
                                        f"  Likely Cause: "
                                        f"{failure_analysis['likely_causes'][0]}"
                                    )
                                if failure_analysis.get("recommendations"):
                                    logger.error(
                                        f"  Recommendation: "
                                        f"{failure_analysis['recommendations'][0]}"
                                    )

                                # Enhanced context already provides detailed info
                                # Show sample log errors if available
                                logs = job_context.get("logs", {})
                                if logs and logs.get("error_patterns"):
                                    logger.error("  Error Patterns:")
                                    for pattern in logs["error_patterns"][
                                        :2
                                    ]:  # First 2 patterns
                                        logger.error(
                                            f"    - {pattern['pattern']}: "
                                            f"{pattern['message'][:80]}..."
                                        )

                                # Show monitoring insights if available
                                mon_cntx = job_context.get("monitoring_context", {})
                                if mon_cntx and mon_cntx.get("monitoring_duration"):
                                    duration = mon_cntx["monitoring_duration"]
                                    logger.error(f"  Monitored for: {duration}")

                                    if mon_cntx.get("log_snapshots"):
                                        logger.error(
                                            f"{len(mon_cntx['log_snapshots'])} "
                                            "log snapshots during monitoring"
                                        )
                        except Exception as detail_error:
                            logger.error(f"Failed to get job details: {detail_error}")

                        raise exec_error
                    snakemake_result = True
                    logger.info("AWS Batch workflow execution succeeded.")

                finally:
                    # Restore AWS credentials to environment
                    if aws_access_key is not None:
                        os.environ["AWS_ACCESS_KEY_ID"] = aws_access_key
                    if aws_secret_key is not None:
                        os.environ["AWS_SECRET_ACCESS_KEY"] = aws_secret_key

            except Exception as e:
                snakemake_result = False
                logger.error(f"AWS Batch workflow execution failed: {e}")
                logger.error(f"Exception details: {str(e)}")
                import traceback

                logger.error(f"Full traceback: {traceback.format_exc()}")
            finally:
                if not snakemake_result:
                    BatchDebug.debug_batch_failure(
                        batch_executor, smk_logger, smk_workdir
                    )
                else:
                    # Stop job monitoring for successful runs
                    logger.info("Stopping enhanced job monitoring...")
                    batch_executor.stop_job_monitoring()
                    smk_logger.extract_errors_from_snakemake_log(smk_workdir)

    except Exception as e:
        print(
            f"DIAGNOSTIC: Exception caught in _snakemake_execute_batch: "
            f"{type(e).__name__}: {e}",
            flush=True,
        )
        logger.error(f"Failed to setup AWS Batch execution: {e}")
        logger.error(f"DIAGNOSTIC: Exception type: {type(e).__name__}")
        import traceback

        print(f"DIAGNOSTIC: Full traceback:\n{traceback.format_exc()}", flush=True)
        snakemake_result = False
    finally:
        # Clean up config symlink if it exists
        config_symlink = "/app/snakemake.yaml"
        if os.path.islink(config_symlink):
            os.unlink(config_symlink)
            logger.debug(f"Cleaned up config symlink: {config_symlink}")

        smk_logger.clean_up()

    # ------------------------------------------------------------
    # Snakemake execution post process
    # ------------------------------------------------------------

    # Delete lock file on success to allow post-processing uploads to S3
    if snakemake_result and RemoteStorageController.is_available():
        RemoteSyncLockFileUtil.delete_sync_lock_file(workspace_id, unique_id)
        logger.info(
            "Deleted sync lock file after successful "
            "batch execution (before post-processing)"
        )

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
