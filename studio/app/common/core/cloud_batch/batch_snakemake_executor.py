"""
Batch Snakemake Executor Module

Handles the complete AWS Batch execution workflow for Snakemake.
This module encapsulates all batch-specific logic for:
- Batch workspace preparation
- S3 configuration and file uploads
- AWS Batch DAG execution
- Job monitoring and error handling
"""

import os
import time
from pathlib import Path

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

from studio.app.common.core.cloud_batch.batch_config import BATCH_CONFIG
from studio.app.common.core.cloud_batch.batch_utils import (
    BatchDebug,
    BatchUtils,
    download_workflow_results_from_s3,
    observe_and_update_node_status_from_s3,
    upload_snakefile_to_s3,
    upload_snakemake_config_to_s3,
    upload_workflow_results_to_s3,
)
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.snakemake.smk import SmkParam
from studio.app.common.core.snakemake.smk_status_logger import SmkStatusLogger
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
)
from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.common.core.workflow.workflow_result import WorkflowResult
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()


class BatchSnakemakeExecutor:
    """Handles AWS Batch execution of Snakemake workflows"""

    def __init__(self, workspace_id: str, unique_id: str):
        self.workspace_id = workspace_id
        self.unique_id = unique_id
        self.batch_executor = None
        self.smk_logger = None

    async def execute_batch_workflow(self, params: SmkParam) -> bool:
        """
        Main entry point for batch workflow execution.

        Orchestrates the complete batch execution workflow:
        1. Upload config and Snakefile to S3
        2. Execute workflow on AWS Batch
        3. Download results from S3
        4. Update node status and observe results

        Args:
            params: Snakemake parameters

        Returns:
            bool: True if execution succeeded, False otherwise
        """
        # Execute the batch workflow
        result = await self._execute_batch(params)

        if result:
            # Upload final workflow results to S3 for persistence
            await upload_workflow_results_to_s3(self.workspace_id, self.unique_id)

            # Download results from S3 to local storage for post-processing
            await download_workflow_results_from_s3(self.workspace_id, self.unique_id)

            # Observe node status by checking S3 files directly and update status
            # This must happen BEFORE local observe to ensure correct status
            try:
                await observe_and_update_node_status_from_s3(
                    self.workspace_id, self.unique_id
                )
                logger.info(
                    "Node status updated from S3 observation for batch execution"
                )
            except Exception as e:
                logger.error(
                    f"S3 status observation failed after batch execution: {e}",
                    exc_info=True,
                )

            # Now that files are downloaded locally, observe workflow results
            # This must happen AFTER S3 download to ensure pickle files exist
            try:
                await WorkflowResult(
                    self.workspace_id, self.unique_id
                ).observe_overall()
                logger.info(
                    "Workflow observation completed after "
                    "S3 download for batch execution"
                )
            except Exception as e:
                logger.error(
                    f"Workflow observation failed after batch execution: {e}",
                    exc_info=True,
                )

        return result

    async def _execute_batch(self, params: SmkParam) -> bool:
        """
        Execute Snakemake workflow using AWS Batch executor.

        Returns:
            bool: True if execution succeeded, False otherwise
        """
        self.smk_logger = SmkStatusLogger(self.workspace_id, self.unique_id)

        # Configure workdir based on storage mode
        smk_workdir = self._get_workdir()

        try:
            # Initialize BatchExecutor for AWS Batch specific operations
            logger.info("Load BatchExecutor")
            self.batch_executor = BatchUtils(self.workspace_id, self.unique_id)
            logger.info("BATCH: BatchUtils object created successfully")

            # Configure S3 bucket early for batch execution
            if not self._configure_s3_bucket():
                return False

            # Upload config and Snakefile to S3
            if not await self._upload_config_and_snakefile():
                return False

            # Prepare workspace for batch execution
            logger.info("Prepare batch workspace")
            self.batch_executor.prepare_batch_workspace()

            # Create config symlink for DAG creation
            self._create_config_symlink()

            # Configure storage settings
            storage_settings = self._get_storage_settings()

            # Execute workflow with AWS Batch
            snakemake_result = await self._execute_workflow_with_batch(
                params, smk_workdir, storage_settings
            )

            # Handle post-execution tasks (lock files, experiment records, data usage)
            snakemake_result = await self._handle_post_execution(snakemake_result)

            return snakemake_result

        except Exception as e:
            logger.error(f"Failed to setup AWS Batch execution: {e}")
            import traceback

            logger.error(f"Full traceback:\n{traceback.format_exc()}")
            return False
        finally:
            self._cleanup_config_symlink()
            if self.smk_logger:
                self.smk_logger.clean_up()

    def _get_workdir(self) -> str:
        """Get the appropriate workdir based on storage mode"""
        if RemoteStorageController.is_available():
            # S3 mode: use /app as workdir
            return "/app"
        else:
            # EFS mode: use absolute path
            return (
                Path(
                    join_filepath(
                        [DIRPATH.OUTPUT_DIR, self.workspace_id, self.unique_id]
                    )
                )
                .resolve()
                .as_posix()
            )

    def _configure_s3_bucket(self) -> bool:
        """Configure S3 bucket name early for batch execution"""
        if not RemoteStorageController.is_available():
            return True

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
        return True

    async def _upload_config_and_snakefile(self) -> bool:
        """Upload config file and Snakefile to S3 for batch jobs"""
        # Upload config
        config_upload_success = await upload_snakemake_config_to_s3(
            self.workspace_id, self.unique_id
        )
        if not config_upload_success:
            logger.error("Failed to upload snakemake config to S3")
            return False

        # Upload Snakefile
        snakefile_upload_success = await upload_snakefile_to_s3(
            self.workspace_id, self.unique_id
        )
        if not snakefile_upload_success:
            logger.error("Failed to upload Snakefile to S3")
            return False

        return True

    def _create_config_symlink(self):
        """Create symlink to config file for DAG creation on main instance"""
        config_source = join_filepath(
            [
                DIRPATH.OUTPUT_DIR,
                self.workspace_id,
                self.unique_id,
                DIRPATH.SNAKEMAKE_CONFIG_YML,
            ]
        )
        config_symlink = "/app/snakemake.yaml"

        # Remove existing symlink/file if present
        if os.path.islink(config_symlink):
            os.unlink(config_symlink)
        elif os.path.exists(config_symlink):
            os.remove(config_symlink)

        # Create symlink
        os.symlink(config_source, config_symlink)
        logger.info(f"Created config symlink: {config_symlink} -> {config_source}")

    def _cleanup_config_symlink(self):
        """Clean up config symlink"""
        config_symlink = "/app/snakemake.yaml"
        if os.path.islink(config_symlink):
            os.unlink(config_symlink)
            logger.info(f"Cleaned up config symlink: {config_symlink}")

    def _get_storage_settings(self) -> StorageSettings:
        """Get storage settings based on availability (S3 or EFS)"""
        if RemoteStorageController.is_available():
            # Use S3 when available
            s3_prefix = BATCH_CONFIG.AWS_DEFAULT_PROVIDER.lower()
            s3_bucket_name = os.environ.get(
                "S3_DEFAULT_BUCKET_NAME", BATCH_CONFIG.AWS_BATCH_S3_BUCKET_NAME
            )
            s3_storage = f"{s3_prefix}://{s3_bucket_name}/app/studio_data"

            return StorageSettings(
                default_storage_provider="s3",
                default_storage_prefix=s3_storage,
                local_storage_prefix=Path(".snakemake/storage"),
                remote_job_local_storage_prefix=Path(".snakemake/storage"),
                shared_fs_usage=frozenset([]),
                retrieve_storage=True,
                keep_storage_local=False,
            )
        else:
            # Use optimized EFS configuration
            logger.warning("S3 not available, configuring optimized EFS storage")
            BatchUtils.prepare_efs_environment(self.workspace_id)
            return BatchUtils.get_efs_optimized_storage_settings(
                self.workspace_id, self.unique_id
            )

    async def _execute_workflow_with_batch(
        self, params: SmkParam, smk_workdir: str, storage_settings: StorageSettings
    ) -> bool:
        """Execute the workflow using AWS Batch"""
        with SnakemakeApi(
            OutputSettings(
                verbose=True,
                show_failed_logs=True,
                debug_dag=True,
                printshellcmds=True,
            ),
        ) as snakemake_api:
            workflow_api = snakemake_api.workflow(
                snakefile=Path(DIRPATH.SNAKEMAKE_FILEPATH),
                workdir=Path(smk_workdir),
                storage_settings=storage_settings,
                resource_settings=ResourceSettings(
                    cores=1,
                    nodes=1,
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
            dag_settings = DAGSettings(forceall=forceall)

            try:
                dag_api = workflow_api.dag(dag_settings=dag_settings)
                logger.info("DAG created successfully")
            except Exception as e:
                logger.error(f"Failed to create DAG: {e}")
                raise e

            logger.info("Starting workflow execution on AWS Batch...")

            try:
                # Execute with batch-specific settings
                result = await self._execute_dag_on_batch(dag_api)
                return result
            except Exception as e:
                logger.error(f"AWS Batch workflow execution failed: {e}")
                logger.error(f"Exception details: {str(e)}")
                import traceback

                logger.error(f"Full traceback: {traceback.format_exc()}")
                return False
            finally:
                if hasattr(self, "_snakemake_result") and not self._snakemake_result:
                    BatchDebug.debug_batch_failure(
                        self.batch_executor, self.smk_logger, smk_workdir
                    )
                else:
                    logger.info("Stopping enhanced job monitoring...")
                    self.batch_executor.stop_job_monitoring()
                    self.smk_logger.extract_errors_from_snakemake_log(smk_workdir)

    async def _execute_dag_on_batch(self, dag_api) -> bool:
        """Execute DAG on AWS Batch with monitoring and error handling"""
        # Get job queue for user
        selected_job_queue = self.batch_executor.get_job_queue_for_user()
        logger.info(f"Using AWS Batch job queue: {selected_job_queue}")

        # Temporarily remove AWS credentials
        aws_access_key = os.environ.pop("AWS_ACCESS_KEY_ID", None)
        aws_secret_key = os.environ.pop("AWS_SECRET_ACCESS_KEY", None)

        try:
            # Prepare environment variables for batch jobs
            envvars = self._prepare_batch_envvars()

            # Prepare container setup commands
            contain_setup = self._prepare_container_setup()

            # Start job monitoring
            logger.info("Starting enhanced job monitoring...")
            self.batch_executor.start_job_monitoring()

            execution_start_time = time.time()
            logger.info(f"Starting DAG execution at {execution_start_time}")

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
                        task_timeout=1800,
                    ),
                    remote_execution_settings=RemoteExecutionSettings(
                        container_image=self.batch_executor.get_container_image(),
                        envvars=envvars,
                        jobname="optinist-{rulename}-{jobid}",
                        precommand=contain_setup if contain_setup else None,
                    ),
                )

                execution_duration = time.time() - execution_start_time
                logger.info(f"DAG execution completed in {execution_duration:.2f}s")
                return True

            except Exception as exec_error:
                execution_duration = time.time() - execution_start_time
                logger.error(f"DAG execution failed after {execution_duration:.2f}s")
                logger.error(f"Execution error: {exec_error}")

                # Log detailed job failure information
                self._log_job_failures()
                raise exec_error

        finally:
            # Restore AWS credentials
            if aws_access_key is not None:
                os.environ["AWS_ACCESS_KEY_ID"] = aws_access_key
            if aws_secret_key is not None:
                os.environ["AWS_SECRET_ACCESS_KEY"] = aws_secret_key
            if "IN_SNAKEMAKE_BATCH" in os.environ:
                del os.environ["IN_SNAKEMAKE_BATCH"]

    def _prepare_batch_envvars(self) -> list:
        """Prepare environment variables for batch jobs"""
        # Set IN_SNAKEMAKE_BATCH
        os.environ["IN_SNAKEMAKE_BATCH"] = "true"
        envvars = ["USE_AWS_BATCH", "OPTINIST_DIR", "IN_SNAKEMAKE_BATCH"]

        if RemoteStorageController.is_available():
            # S3 mode environment variables
            batch_s3_bucket = os.environ.get(
                "S3_DEFAULT_BUCKET_NAME",
                BATCH_CONFIG.AWS_BATCH_S3_BUCKET_NAME,
            )

            if not batch_s3_bucket:
                raise ValueError(
                    "S3_DEFAULT_BUCKET_NAME environment variable must "
                    "be set for AWS Batch execution with S3 storage"
                )

            os.environ["AWS_BATCH_S3_BUCKET_NAME"] = batch_s3_bucket
            logger.info(f"Configured AWS Batch S3 bucket: {batch_s3_bucket}")

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
            # EFS mode environment variables
            os.environ["EFS_MOUNT_TARGET"] = DIRPATH.DATA_DIR
            os.environ["TMPDIR"] = "/tmp"
            os.environ["TMP"] = "/tmp"
            envvars.extend(["EFS_MOUNT_TARGET", "TMPDIR", "TMP"])
            logger.info("Using EFS storage for batch jobs")

        return envvars

    def _prepare_container_setup(self) -> str:
        """Prepare container setup commands based on storage mode"""
        if RemoteStorageController.is_available():
            # S3 mode: download config and Snakefile from S3
            contain_setup = BatchUtils.get_s3_container_setup_commands(
                self.workspace_id, self.unique_id
            )
            logger.info("Using S3 container setup (config/Snakefile download)")
        else:
            # EFS mode: full container setup
            contain_setup = BatchUtils.get_container_setup_commands(
                self.workspace_id, self.unique_id
            )
            logger.info("Using EFS container setup (full environment)")

        # Flatten and join commands
        if contain_setup:
            flattened_commands = []
            for cmd in contain_setup:
                if isinstance(cmd, list):
                    flattened_commands.extend(str(subcmd) for subcmd in cmd)
                else:
                    flattened_commands.append(str(cmd))
            contain_setup = " && ".join(flattened_commands)
            logger.info(f"Joined precommand: {contain_setup}")

        return contain_setup

    def _log_job_failures(self):
        """Log detailed information about failed batch jobs"""
        try:
            recent_failed = self.batch_executor.get_recent_failed_jobs(
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
                    exit_reason = job_context.get("exit_reason", "Unknown")
                    logger.error(f"Exit Code: {exit_code}, Reason: {exit_reason}")

                    # Show failure analysis
                    failure_analysis = job_context.get("failure_analysis", {})
                    if failure_analysis.get("likely_causes"):
                        logger.error(
                            f"Likely Cause: {failure_analysis['likely_causes'][0]}"
                        )
                    if failure_analysis.get("recommendations"):
                        logger.error(
                            f"Recommendation: {failure_analysis['recommendations'][0]}"
                        )

        except Exception as detail_error:
            logger.error(f"Failed to get job details: {detail_error}")

    async def _handle_post_execution(self, snakemake_result: bool) -> bool:
        """Handle post-execution tasks for batch workflow

        Args:
            snakemake_result: Result of snakemake execution

        Returns:
            bool: The snakemake_result (passed through)
        """
        from studio.app.common.core.experiment.experiment_record_services import (
            ExperimentRecordService,
        )
        from studio.app.common.core.storage.remote_storage_controller import (
            RemoteSyncAction,
            RemoteSyncLockFileUtil,
            RemoteSyncStatusFileUtil,
        )
        from studio.app.common.core.workspace.workspace_data_capacity_services import (
            WorkspaceDataCapacityService,
        )

        # Delete lock file on success to allow post-processing uploads to S3
        if snakemake_result and RemoteStorageController.is_available():
            RemoteSyncLockFileUtil.delete_sync_lock_file(
                self.workspace_id, self.unique_id
            )
            logger.info(
                "Deleted sync lock file after successful "
                "batch execution (before post-processing)"
            )

        try:
            # Update workflow processing results
            # NOTE: In batch mode, observe_overall() is deferred until AFTER S3 download
            # completes (see execute_batch_workflow() method). This ensures pickle files
            # exist locally before observation. Observing here would cause false errors
            # because files are still in S3.

            # Update experiment database record
            if ExperimentRecordService.is_available():
                ExperimentRecordService.regist_record_on_workflow_completed(
                    self.workspace_id, self.unique_id
                )

            # Data usage calculation
            WorkspaceDataCapacityService.update_experiment_data_usage(
                self.workspace_id, self.unique_id
            )
        except Exception as e:
            logger.error(f"Batch post process failed: {e}", exc_info=True)

        # result error handling
        if not snakemake_result:
            # Operate remote storage.
            if RemoteStorageController.is_available():
                # force delete sync lock file
                RemoteSyncLockFileUtil.delete_sync_lock_file(
                    self.workspace_id, self.unique_id
                )

                remote_bucket_name = RemoteSyncStatusFileUtil.get_remote_bucket_name(
                    self.workspace_id, self.unique_id
                )

                # force update sync status file
                RemoteSyncStatusFileUtil.create_sync_status_file_for_error(
                    remote_bucket_name,
                    self.workspace_id,
                    self.unique_id,
                    RemoteSyncAction.UPLOAD,
                )

        return snakemake_result
