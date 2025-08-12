import os
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy.exc import NoResultFound
from sqlmodel import Session, delete

from studio.app.common.core.experiment.experiment import ExptConfig
from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.mode import MODE
from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.common.core.workflow.workflow import NodeRunStatus, NodeType
from studio.app.common.core.workflow.workflow_reader import WorkflowConfigReader
from studio.app.common.db.database import session_scope
from studio.app.common.models.experiment import ExperimentRecord
from studio.app.common.schemas.dataview import DataviewThumbnails
from studio.app.common.schemas.workflow import WorkflowConfig
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()


class ExperimentRecordService:
    @classmethod
    def is_available(cls) -> bool:
        # ExperimentRecordService is available in multiuser mode
        available = MODE.IS_MULTIUSER
        return available

    @classmethod
    def regist_record_on_workflow_completed(cls, workspace_id: str, unique_id: str):
        """
        Processing upon workflow completion
        """

        experiment_config = ExptConfigReader.read(workspace_id, unique_id)

        # Make data to be registered
        thumbnails = cls.__make_dataview_thumnail_paths(
            workspace_id, unique_id, experiment_config
        )
        workflow_success = experiment_config.success == NodeRunStatus.SUCCESS.value
        analyzed_at = experiment_config.finished_at or experiment_config.started_at

        # Update ExperimentRecord to database
        with session_scope() as db:
            try:
                exp = (
                    db.query(ExperimentRecord)
                    .filter(
                        ExperimentRecord.workspace_id == workspace_id,
                        ExperimentRecord.uid == unique_id,
                    )
                    .one()
                )
                exp.name = experiment_config.name
                exp.thumbnails = dict(thumbnails)
                exp.success = workflow_success
                exp.analyzed_at = analyzed_at

            except NoResultFound:
                exp = ExperimentRecord(
                    workspace_id=workspace_id,
                    uid=unique_id,
                    name=experiment_config.name,
                    thumbnails=dict(thumbnails),
                    success=workflow_success,
                    analyzed_at=analyzed_at,
                )
                db.add(exp)

    @classmethod
    def delete_record(
        cls, db: Session, workspace_id: str, unique_id: str, auto_commit: bool = False
    ):
        db.execute(
            delete(ExperimentRecord).where(
                ExperimentRecord.workspace_id == workspace_id,
                ExperimentRecord.uid == unique_id,
            )
        )

        if auto_commit:
            db.commit()

    @classmethod
    def copy_record(
        cls,
        db: Session,
        workspace_id: str,
        unique_id: str,
        new_unique_id: str,
        new_name: str,
        auto_commit: bool = False,
    ):
        try:
            exp = (
                db.query(ExperimentRecord)
                .filter(
                    ExperimentRecord.workspace_id == workspace_id,
                    ExperimentRecord.uid == unique_id,
                )
                .one()
            )

            # Create new record by copying all attributes except primary key
            new_exp_data = {}
            for column in ExperimentRecord.__table__.columns:
                if column.name not in ["id"]:
                    new_exp_data[column.name] = getattr(exp, column.name)

            # Override specific columns
            new_exp_data["uid"] = new_unique_id
            new_exp_data["name"] = new_name
            new_exp_data["publish_status"] = False

            new_exp = ExperimentRecord(**new_exp_data)
            db.add(new_exp)

            if auto_commit:
                db.commit()

        except NoResultFound:
            # If it fails roll back the transaction
            logger.error(
                f"Experiment [{unique_id}] not found in workspace [{workspace_id}]"
            )

    @classmethod
    def update_name(cls, workspace_id: str, unique_id: str, new_name: str):
        with session_scope() as db:
            try:
                exp = (
                    db.query(ExperimentRecord)
                    .filter(
                        ExperimentRecord.workspace_id == workspace_id,
                        ExperimentRecord.uid == unique_id,
                    )
                    .one()
                )
                exp.name = new_name

            except Exception as e:
                db.rollback()
                logger.error(e, exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to update ExperimentRecord"
                    f" [{workspace_id}/{unique_id}].",
                )

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
                cls.regist_record_on_workflow_completed(workspace_id, unique_id)
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
    def __make_dataview_thumnail_paths(
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
