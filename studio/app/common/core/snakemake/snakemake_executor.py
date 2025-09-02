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
    if BATCH_CONFIG.USE_AWS_BATCH:
        logger.info("Starting AWS Batch execution mode")
        future_result = _snakemake_execute_batch(workspace_id, unique_id, params)
        # Handle S3 operations for batch execution (due to separate EFS systems)
        if future_result:
            # First upload results from batch EFS to S3
            asyncio.run(upload_workflow_results_to_s3(workspace_id, unique_id))
            # Then download them to main EFS for post-processing
            asyncio.run(download_workflow_results_from_s3(workspace_id, unique_id))
    else:
        logger.info("Starting local execution mode")
        with ProcessPoolExecutor(max_workers=1) as executor:
            logger.info("start snakemake running process.")

            future = executor.submit(
                _snakemake_execute_process, workspace_id, unique_id, params
            )
            future_result = future.result()

    # Update user storage after workflow completion
    asyncio.run(update_user_storage_after_workflow(workspace_id))

    return future_result


def _snakemake_execute_process(
    workspace_id: str, unique_id: str, params: SmkParam
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


def _snakemake_execute_batch(
    workspace_id: str, unique_id: str, params: SmkParam
) -> bool:
    """
    Execute Snakemake workflow using AWS Batch executor.
    """

    smk_logger = SmkStatusLogger(workspace_id, unique_id)
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
        logger.info("Load BatchExecutor")
        batch_executor = BatchUtils(workspace_id, unique_id)

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

        # Configure storage based on availability
        # Force EFS usage instead of S3 to avoid Snakemake S3 configuration issues
        storage_settings = None
        if (
            False
        ):  # Temporarily disable S3 to use EFS: RemoteStorageController.is_available():
            # Use S3 when available
            s3_prefix = BATCH_CONFIG.AWS_DEFAULT_PROVIDER.lower()
            s3_bucket_name = os.environ.get(
                "S3_DEFAULT_BUCKET_NAME", BATCH_CONFIG.AWS_BATCH_S3_BUCKET_NAME
            )
            # Configure S3 storage mapping
            # Snakemake local paths: /app/studio_data/output/1/958d5ef3/file.pkl
            # Should map to S3: s3://bucket/app/studio_data/output/1/958d5ef3/file.pkl
            # But we're seeing: s3://bucket//app/studio_data/... (double slash)
            # Tried 1:
            # s3_storage = f"{s3_prefix}://{s3_bucket_name}" ...
            # + local_storage_prefix=Path(DIRPATH.DATA_DIR),
            # resulted in exit at STARTING with no files found:
            # s3://subscr-optinist-app-storage//app/studio_data/output/1/...
            # 8b445935/input_zdax4o54o0/sample_mouse2p_behavior.pkl
            # + batch_workdir = Path("/app")
            # Tried 2:
            # s3_storage = f"{s3_prefix}://{s3_bucket_name}/app/studio_data"
            # + local_storage_prefix=Path(DIRPATH.DATA_DIR),
            # + batch_workdir = Path("/app")
            # resulted in exit at STARTING with no files found:
            # /app/studio_data/s3/subscr-optinist-app-storage/...
            # app/studio_data/app/studio_data/input/1
            # Tried 3:
            # s3_storage = f"{s3_prefix}://{s3_bucket_name}" # no local_storage_prefix
            # + batch_workdir = Path("/app")
            # resulted in exit at STARTING with no files found:
            # s3://subscr-optinist-app-storage//app/studio_data/...
            # output/1/c75c5320/input_zdax4o54o0/sample_mouse2p_behavior.pkl
            # Tried 4:
            # s3_storage = f"{s3_prefix}://{s3_bucket_name}"
            # + local_storage_prefix=Path(DIRPATH.DATA_DIR),
            # + batch_workdir = Path(DIRPATH.DATA_DIR)
            # resulted in:
            # s3://subscr-optinist-app-storage//app/studio_data/...
            # output/1/9196bec2/input_zdax4o54o0/sample_mouse2p_behavior.pkl
            # And also /app/studio_data/s3/subscr-optinist-app-storage/app/...
            # studio_data/output/1/9196bec2/input_ab1mmvt2ky/sample_mouse2p_image.pkl
            # So next will try:
            # s3_storage = f"{s3_prefix}://{s3_bucket_name}"
            # + batch_workdir = Path("/app") # Keep Snakefile accessible
            # + NO local_storage_prefix # Avoid duplication
            # Tried 5:
            # s3_storage = f"{s3_prefix}://{s3_bucket_name}"
            # + shared_fs_usage=[], retrieve_storage=True, keep_storage_local=False
            # + Upload snakemake.yaml config to S3 for batch jobs to find
            # This should fix the "Invalid config yaml file" error
            # Tried 6:
            # s3_storage = f"{s3_prefix}://{s3_bucket_name}" (no trailing slash)
            # + local_storage_prefix=Path("/app") (/app instead of /app/studio_data)
            # Result: Still double slash, Snakemake creating /app/s3/ paths
            # Tried 7:
            # s3_storage = f"{s3_prefix}://{s3_bucket_name}"
            # + remote_job_local_storage_prefix for AWS Batch jobs
            # + No local_storage_prefix to avoid local S3 mount paths
            # Result: Still double slash s3://bucket//app/studio_data/...
            # Tried 8:
            # Empty default_storage_prefix to bypass Snakemake's path construction
            # + frozenset() for shared_fs_usage (not list)
            # + Let S3 plugin handle full path construction
            # Result: ERROR - S3 plugin requires valid --default-storage-prefix
            # with s3:// scheme
            # Tried 9:
            # s3_storage = f"{s3_prefix}://{s3_bucket_name}/app/studio_data"
            # + default_storage_prefix includes full /app/studio_data path
            # + local_storage_prefix=Path(DIRPATH.DATA_DIR) strips /app/studio_data
            # + remote_job_local_storage_prefix="/tmp/snakemake_scratch"
            # Result: Triple duplication
            # /app/studio_data/s3/.../app/studio_data/app/studio_data/
            # Tried 10:
            # s3_storage = f"{s3_prefix}://{s3_bucket_name}" (no path in prefix)
            # + local_storage_prefix=Path(DIRPATH.DATA_DIR) strips /app/studio_data
            # + remote_job_local_storage_prefix="/tmp/snakemake_scratch" for batch jobs
            # + frozenset() for shared_fs_usage
            # Similar to Tried 1 but adds remote_job_local_storage_prefix
            # Result: SAME as # 1 - Double slash s3://bucket//app/studio_data/output/...
            # Jobs submit but still path construction issue + S3 mount paths
            # Tried 11:
            # s3_storage = f"{s3_prefix}://{s3_bucket_name}" (no path in prefix)
            # + local_storage_prefix=Path("/tmp/snakemake_storage") (different from
            #    container data dir)
            # + remote_job_local_storage_prefix=Path("/tmp/snakemake_storage")
            #    (same as local)
            # + Avoid path conflicts with actual container working directory
            # Result: Better local storage (/tmp/snakemake_storage/s3/...) but STILL
            # double slash s3://bucket//app/studio_data/...
            # Tried 12:
            # + Modified SmkUtils to generate relative paths for S3 mode
            # + s3_storage includes full app/studio_data path prefix
            # + SmkUtils strips /app/studio_data from absolute paths in S3 mode
            # + Relative paths: "output/1/abc/file.pkl" + prefix
            # + Expected result: s3://bucket/app/studio_data/output/1/abc/file.pkl
            # And workdir=Path(smk_workdir)
            # Result: MissingInputException - path duplication still occurring.
            # /tmp/snakemake_storage/s3/subscr-optinist-app-storage/...
            # app/studio_data/app/studio_data/output/...
            # Tried 13:
            # + s3_storage = f"{s3_prefix}://{s3_bucket_name}" (no path in prefix)
            # + Let Snakemake combine the bucket URI with the full
            # relative path from the rule
            # + Expected result: s3://bucket/app/studio_data/output/1/abc/file.pkl
            # Result: MissingInputException - path duplication still occurring.
            # /tmp/snakemake_storage/s3/subscr-optinist-app-storage/app/studio_data/output/
            # Tried 14:
            # Fix storage prefix to include OPTINIST_DIR path from environment
            # s3_storage=f"{s3_prefix}://{s3_bucket_name}/{DIRPATH.DATA_DIR.lstrip("/")}"
            # SmkUtils converts:
            # /app/studio_data/output/1/abc/file.pkl -> output/1/abc/file.pkl
            # Storage prefix should point to where relative paths should be stored in S3
            # Use DIRPATH.DATA_DIR which comes from OPTINIST_DIR environment variable
            # Result: Duplicated /app/studio_data//app/studio_data paths in S3
            # Tried 15:
            # Use EFS/local storage for intermediate files, avoid S3 storage system
            # + Remove default_storage_provider and default_storage_prefix
            # + Set shared_fs_usage=frozenset(["s3"]) to treat S3 as shared filesystem
            # + SmkUtils.input() and output() return absolute paths directly
            # + Upload final results to S3 after workflow completion using
            # upload_experiment_wrapper()
            # + Expected result: No path duplication, faster intermediate I/O,
            # final results in S3
            # This completely bypasses all the path duplication issues with
            # Snakemake's S3 storage
            data_dir_path = (
                Path(DIRPATH.DATA_DIR).resolve().as_posix().lstrip("/")
            )  # Remove leading slash for S3 path
            s3_storage = f"{s3_prefix}://{s3_bucket_name}/{data_dir_path}"

            logger.debug(f"DIRPATH.DATA_DIR from OPTINIST_DIR: {DIRPATH.DATA_DIR}")
            logger.debug(f"Stripped data_dir_path for S3: {data_dir_path}")
            logger.debug(f"Constructed S3 storage prefix: {s3_storage}")
            logger.debug(
                f"Expected path mapping: output/1/abc/file.pkl -> "
                f"{s3_storage}/output/1/abc/file.pkl"
            )

            storage_settings = StorageSettings(
                local_storage_prefix=Path("/tmp/snakemake_storage").resolve(),
                remote_job_local_storage_prefix=Path(
                    "/tmp/snakemake_storage"
                ).resolve(),
                shared_fs_usage=frozenset(["s3"]),
                retrieve_storage=True,
                keep_storage_local=False,
            )
            # Debug S3 storage configuration (uncomment to enable)
            # BatchDebug.debug_s3_storage_config(s3_storage, s3_bucket_name, s3_prefix)

            logger.debug(f"Using S3 storage: {s3_storage}")
            logger.debug(
                f"S3 storage breakdown: provider='{s3_prefix}', "
                f"bucket='{s3_bucket_name}', full_prefix='{s3_storage}'"
            )
            logger.debug("Local storage prefix: /tmp/snakemake_storage")
            logger.debug(f"DIRPATH.DATA_DIR: {DIRPATH.DATA_DIR}")
            logger.debug(
                f"Path conversion flow: {DIRPATH.DATA_DIR}/output/1/abc/file.pkl "
                f"-> (SmkUtils) -> output/1/abc/file.pkl "
                f"-> (Snakemake) -> {s3_storage}/output/1/abc/file.pkl"
            )
        else:
            # Use optimized EFS configuration when S3 is not available
            logger.info("S3 not available, configuring optimized EFS storage")
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
            logger.info("=" * 60)
            logger.info("DRYRUN VALIDATION OUTPUT:")
            logger.info("=" * 60)

            try:
                logger.info("Starting dryrun validation with existing DAG")

                # Prepare environment variables for dryrun (same as real execution)
                dryrun_envvars = ["USE_AWS_BATCH", "OPTINIST_DIR"]
                if (
                    False
                ):  # Temporarily disable S3 for dryrun, use EFS RemoteStorageController
                    dryrun_envvars.extend(
                        [
                            "S3_DEFAULT_BUCKET_NAME",
                            "AWS_DEFAULT_REGION",
                            "PYTHONPATH",
                        ]
                    )
                    logger.info("Using S3 storage for dryrun validation")
                else:
                    dryrun_envvars.extend(["EFS_MOUNT_TARGET", "TMPDIR", "TMP"])
                    logger.info("Using EFS storage for dryrun validation")

                # Execute verbose dryrun validation
                dag_api.execute_workflow(
                    executor="dryrun",  # Use same executor for validation
                    execution_settings=ExecutionSettings(
                        retries=0,  # No retries needed for dryrun
                        keep_going=True,  # Continue validation even if some rules fail
                        latency_wait=0,  # Reduce latency wait for faster dryrun
                    ),
                )

                logger.info("=" * 60)
                logger.info("✅ Dryrun validation passed - workflow structure is valid")

            except Exception as dryrun_error:
                logger.error("=" * 60)
                logger.error("❌ Dryrun validation failed")
                logger.error(f"Dryrun error: {dryrun_error}")

                # Enhanced error reporting with full traceback
                import traceback

                logger.error("Full traceback:")
                logger.error(traceback.format_exc())
                logger.error("=" * 60)

                # Decision point: fail fast or continue with warning
                logger.error(
                    "⚠️  Dryrun validation failed - batch execution may also fail"
                )
                logger.error(
                    "This indicates workflow configuration issues "
                    "(likely S3 path mapping)"
                )
                logger.error("Consider fixing workflow issues before proceeding")

                # Uncomment the next line to fail fast on dryrun errors:
                raise Exception(
                    f"Batch execution aborted due to dryrun "
                    f"validation failure: {dryrun_error}"
                )

                # For now, continue with warning to maintain existing behavior
                # logger.warning(
                #     "Proceeding with batch execution despite dryrun warnings"
                # )

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
                    # Prepare environment variables for batch jobs
                    envvars = ["USE_AWS_BATCH", "OPTINIST_DIR"]
                    if False:  # Temporarily disable S3 for batch jobs
                        # use EFS RemoteStorageController
                        envvars.extend(
                            [
                                "S3_DEFAULT_BUCKET_NAME",
                                "AWS_DEFAULT_REGION",
                                "PYTHONPATH",
                            ]
                        )
                        logger.info("Using S3 storage for batch jobs")
                    else:
                        envvars.extend(["EFS_MOUNT_TARGET", "TMPDIR", "TMP"])
                        logger.info("Using EFS storage for batch jobs")

                    # Prepare container setup for EFS optimization
                    contain_setup = []
                    if (
                        not False
                    ):  # Temporarily disable S3 for, use EFS + RemoteStorageController
                        contain_setup = BatchUtils.get_container_setup_commands(
                            workspace_id, unique_id
                        )

                    # Debug container command configuration (uncomment to enable)
                    # BatchDebug.debug_container_command(batch_executor, contain_setup)

                    # Debug AWS Batch execution (uncomment to enable)
                    # BatchDebug.debug_aws_batch_execution(
                    #     batch_executor, selected_job_queue, envvars, contain_setup
                    #     )

                    # Check current environment variables that will be passed
                    for env_var in envvars:
                        value = os.environ.get(env_var, "NOT_SET")
                        logger.debug(f"Env {env_var}: {value}")

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
        logger.error(f"Failed to setup AWS Batch execution: {e}")
        snakemake_result = False
    finally:
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
