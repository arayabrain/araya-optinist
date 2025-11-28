"""
Batch Observation Module

Provides high-level interfaces for observing workflow execution in AWS Batch.
This module handles:
- Batch-based workflow observation using S3
- Batch job failure checking
- Node status updates from S3 storage
"""

from typing import Dict, List

from studio.app.common.core.cloud_batch.batch_config import BATCH_CONFIG
from studio.app.common.core.cloud_batch.batch_utils import (
    check_batch_job_failures_throttled,
    observe_and_update_node_status_from_s3,
)
from studio.app.common.core.experiment.experiment import ExptConfig
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
)
from studio.app.common.core.workflow.workflow import Message
from studio.app.common.schemas.workflow import WorkflowErrorInfo

logger = AppLogger.get_logger()


class BatchObservationHandler:
    """Handles observation of workflows running in AWS Batch"""

    @classmethod
    async def observe_batch_nodes_from_s3(
        cls,
        workspace_id: str,
        unique_id: str,
        observe_node_ids: List[str],
        expt_config: ExptConfig,
    ) -> Dict[str, Message]:
        """Observe node status by reading from S3 and construct results

        In batch mode, update node status from S3 and return results from config.
        This reads pickle files directly from S3 and updates experiment.yaml.

        Args:
            workspace_id: Workspace identifier
            unique_id: Unique workflow identifier
            observe_node_ids: List of node IDs to observe
            expt_config: Experiment configuration

        Returns:
            Dict mapping node_id to Message with status and outputs
        """
        # Update node status from S3
        await observe_and_update_node_status_from_s3(workspace_id, unique_id)

        # In batch mode, construct results directly from config
        # Local files may not be downloaded yet, so skip local observation
        node_results: Dict[str, Message] = {}
        for node_id in observe_node_ids:
            expt_function = (
                expt_config.procs.get(node_id)
                if cls._is_procs_node(node_id)
                else expt_config.function.get(node_id)
            )
            if expt_function:
                # Construct Message from config data
                node_results[node_id] = Message(
                    status=expt_function.success,
                    message=expt_function.message
                    or f"{node_id} {expt_function.success}",
                    outputPaths=expt_function.outputPaths,
                )
        return node_results

    @classmethod
    async def check_batch_job_failures(
        cls,
        workspace_id: str,
        unique_id: str,
        observe_node_ids: List[str],
    ) -> WorkflowErrorInfo:
        """Check if any batch jobs have failed for the given workflow

        Throttled to every 60 seconds to avoid excessive AWS API calls.

        Args:
            workspace_id: Workspace identifier
            unique_id: Unique workflow identifier
            observe_node_ids: List of node IDs to check

        Returns:
            WorkflowErrorInfo with error details if any jobs failed
        """
        return await check_batch_job_failures_throttled(
            workspace_id, unique_id, observe_node_ids
        )

    @classmethod
    def should_use_batch_observation(cls) -> bool:
        """Determine if batch observation should be used

        Returns:
            bool: True if batch mode is enabled and remote storage is available
        """
        return BATCH_CONFIG.USE_AWS_BATCH and RemoteStorageController.is_available()

    @staticmethod
    def _is_procs_node(node_id: str) -> bool:
        """Check if node_id corresponds to a process node"""
        from studio.app.common.core.workflow.workflow import ProcessType

        return node_id == ProcessType.POST_PROCESS.id
