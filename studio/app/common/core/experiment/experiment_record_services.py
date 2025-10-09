from fastapi import HTTPException, status
from sqlalchemy.exc import NoResultFound
from sqlmodel import Session, delete

from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.mode import MODE
from studio.app.common.core.workflow.workflow import NodeRunStatus
from studio.app.common.db.database import session_scope
from studio.app.common.models.experiment import ExperimentRecord

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

        from studio.app.common.core.dataview.dataview_services import DataviewService

        experiment_config = ExptConfigReader.read(workspace_id, unique_id)

        # Make data to be registered
        thumbnails = DataviewService.make_dataview_thumnail_paths(
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
        _new_name: str,
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
            new_exp_data["name"] = _new_name
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
