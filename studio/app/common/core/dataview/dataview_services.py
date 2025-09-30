import os
from pathlib import Path
from typing import List

from sqlmodel import Session, delete

from studio.app.common.core.experiment.experiment import ExptConfig
from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
from studio.app.common.core.experiment.experiment_record_services import (
    ExperimentRecordService,
)
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.common.core.workflow.workflow import NodeType
from studio.app.common.core.workflow.workflow_reader import WorkflowConfigReader
from studio.app.common.db.database import session_scope
from studio.app.common.models.experiment import ExperimentRecord
from studio.app.common.models.user import User
from studio.app.common.models.workspace import Workspace
from studio.app.common.schemas.dataview import (
    DataviewThumbnails,
    PublishFlags,
    PublishStatus,
)
from studio.app.common.schemas.workflow import WorkflowConfig
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()


class DataviewService:
    @classmethod
    def find_published_dataview_record(
        cls, db: Session, workspace_id: int, unique_id: str
    ) -> ExperimentRecord:
        record: ExperimentRecord = (
            db.query(ExperimentRecord)
            .join(
                Workspace,
                Workspace.id == ExperimentRecord.workspace_id,
            )
            .filter(
                Workspace.deleted.is_(False),
                ExperimentRecord.workspace_id == int(workspace_id),
                ExperimentRecord.uid == unique_id,
                ExperimentRecord.publish_status == PublishStatus.on.value,
            )
            .first()
        )

        return record

    @classmethod
    def find_user_owned_dataview_record(
        cls, db: Session, record_id: int, user_id: int
    ) -> ExperimentRecord:
        record: ExperimentRecord = (
            db.query(ExperimentRecord)
            .join(
                Workspace,
                Workspace.id == ExperimentRecord.workspace_id,
            )
            .join(
                User,
                User.id == Workspace.user_id,
            )
            .filter(
                ExperimentRecord.id == record_id,
                User.id == user_id,
                User.active.is_(True),
            )
            .first()
        )

        return record

    @classmethod
    def multiple_publish_dataview_records(
        cls,
        db: Session,
        user_id: int,
        ids: List[int],
        flag: PublishFlags,
    ):
        db.query(ExperimentRecord).filter(
            Workspace.id == ExperimentRecord.workspace_id,
            User.id == Workspace.user_id,
            User.id == user_id,
            User.active.is_(True),
            ExperimentRecord.id.in_(ids),
        ).update(
            {ExperimentRecord.publish_status: int(flag == PublishFlags.on)},
            synchronize_session=False,
        )

        db.commit()

    @classmethod
    def sync_dataview_records_for_workspace(
        cls, workspace_id: str, delete_existing: bool = False
    ):
        """
        Sync dataview records for a specific workspace

        Args:
            workspace_id: The workspace ID to sync
            delete_existing: If True, delete all existing records before syncing
        """
        workspace_output_dir = join_filepath([DIRPATH.OUTPUT_DIR, workspace_id])

        if not os.path.exists(workspace_output_dir):
            logger.warning(f"Output directory does not exist: [{workspace_output_dir}]")
            return 0, 0

        # Delete existing records if requested
        if delete_existing:
            with session_scope() as db:
                deleted_count = db.execute(
                    delete(ExperimentRecord).where(
                        ExperimentRecord.workspace_id == workspace_id
                    )
                ).rowcount
                logger.info(
                    f"Deleted {deleted_count} existing records"
                    f" for workspace [{workspace_id}]"
                )

        success_count = 0
        error_count = 0

        # Iterate through all experiment directories
        for exp_folder in Path(workspace_output_dir).iterdir():
            if not exp_folder.is_dir():
                continue

            unique_id = exp_folder.name

            try:
                ExperimentRecordService.regist_record_on_workflow_completed(
                    workspace_id, unique_id
                )
                success_count += 1
                logger.info(f"Successfully synced record: [{workspace_id}/{unique_id}]")

            except Exception as e:
                error_count += 1
                logger.error(
                    f"Failed to sync record: [{workspace_id}/{unique_id}] - {str(e)}",
                    exc_info=True,
                )

        logger.info(
            f"Workspace [{workspace_id}] sync completed. "
            f"Success: {success_count}, Errors: {error_count}"
        )
        return success_count, error_count

    @classmethod
    def make_dataview_thumnail_paths(
        cls,
        workspace_id: str,
        unique_id: str,
        experiment_config_: ExptConfig = None,
        workflow_config_: WorkflowConfig = None,
    ) -> DataviewThumbnails:
        """
        Create values to set in DataviewThumbnails
        *Constructed from ExptConfig and WorkflowConfig
        """

        # Make input data (image) thumbnails path (from ExptConfig)
        image_url = None
        workflow_config = (
            workflow_config_
            if workflow_config_
            else WorkflowConfigReader.read(workspace_id, unique_id)
        )
        for _, node in workflow_config.nodeDict.items():
            if node.type == NodeType.IMAGE:
                image_url = node.data.path[0]
                break

        # Make output data (roi) thumbnails path (from WorkflowConfig)
        roi_url = None
        experiment_config = (
            experiment_config_
            if experiment_config_
            else ExptConfigReader.read(workspace_id, unique_id)
        )
        for _, function in experiment_config.function.items():
            if function.outputPaths and ("cell_roi" in function.outputPaths):
                roi_url = function.outputPaths["cell_roi"].path
                break

        return DataviewThumbnails(
            image_url=image_url,
            roi_url=roi_url,
        )
