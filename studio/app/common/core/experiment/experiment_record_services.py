from fastapi import HTTPException, status
from sqlalchemy.exc import NoResultFound
from sqlmodel import Session, delete

from studio.app.common.core.experiment.experiment import ExptConfig
from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.mode import MODE
from studio.app.common.core.workflow.workflow import NodeRunStatus, NodeType
from studio.app.common.core.workflow.workflow_reader import WorkflowConfigReader
from studio.app.common.db.database import session_scope
from studio.app.common.models.experiment import ExperimentRecord
from studio.app.common.schemas.dataview import DataviewThumbnails
from studio.app.common.schemas.workflow import WorkflowConfig

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

            except NoResultFound:
                exp = ExperimentRecord(
                    workspace_id=workspace_id,
                    uid=unique_id,
                    name=experiment_config.name,
                    thumbnails=dict(thumbnails),
                    success=workflow_success,
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
            new_exp = ExperimentRecord(
                workspace_id=workspace_id,
                uid=new_unique_id,
                data_usage=exp.data_usage,
            )
            db.add(new_exp)

            if auto_commit:
                db.commit()

        except NoResultFound:
            # If it fails roll back the transaction
            logger.error(
                f"Experiment {unique_id} not found in workspace {workspace_id}"
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
