import json
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import Request
from sqlalchemy import func
from sqlmodel import Session, delete

from studio.app.common.core.experiment.experiment import ExptConfig, ExptOutputPathIds
from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
from studio.app.common.core.experiment.experiment_record_services import (
    ExperimentRecordService,
)
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.utils.filepath_creater import (
    create_directory,
    join_filepath,
    normalize_output_path,
)
from studio.app.common.core.dataview.dataview import DatasetPaths
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
    PublishValidationResult,
)
from studio.app.common.schemas.workflow import WorkflowConfig
from studio.app.const import ThumbnailType
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()


class PublishValidator:
    """
    Central validation for experiment publishing.

    Validates that an experiment can be published and/or displayed.
    Consolidates all publish-related checks in one place.
    """

    @classmethod
    def validate(
        cls,
        workspace_id: str,
        unique_id: str,
        user_has_s3_bucket: bool = True,
        check_files_on_disk: bool = True,
    ) -> PublishValidationResult:
        """
        Validate whether an experiment can be published.

        Args:
            workspace_id: The workspace ID
            unique_id: The experiment unique ID
            user_has_s3_bucket: Whether the user has an S3 bucket configured
            check_files_on_disk: Whether to check if output files exist on disk

        Returns:
            PublishValidationResult with validation status
        """
        # Check 1: S3 bucket configured
        if not user_has_s3_bucket:
            return PublishValidationResult(
                can_publish=False,
                is_displayable=True,  # Can still view locally
                reason=(
                    "Cannot publish data: No cloud storage bucket configured "
                    "for your account. Please contact support to enable publishing."
                ),
            )

        # Check 2: experiment.yaml exists
        config_path = ExptConfigReader.get_config_yaml_path(workspace_id, unique_id)
        if not os.path.exists(config_path):
            return PublishValidationResult(
                can_publish=False,
                is_displayable=False,
                reason=(
                    "Experiment configuration file is missing. "
                    "The experiment may not have completed successfully."
                ),
            )

        # Check 3: experiment.yaml is valid and not corrupted
        try:
            config = ExptConfigReader.read(workspace_id, unique_id)
        except (AssertionError, KeyError, TypeError) as e:
            logger.warning(
                f"Corrupted experiment config for {workspace_id}/{unique_id}: {e}"
            )
            return PublishValidationResult(
                can_publish=False,
                is_displayable=False,
                reason=(
                    "Experiment configuration is corrupted or invalid. "
                    "The experiment data cannot be displayed."
                ),
            )

        # Check 4: Validate config has required fields
        if not ExptConfigReader.validate_experiment_config(config):
            return PublishValidationResult(
                can_publish=False,
                is_displayable=False,
                reason=(
                    "Experiment configuration is incomplete. "
                    "Required fields are missing."
                ),
            )

        # Check 5: Experiment completed successfully
        from studio.app.common.core.workflow.workflow import WorkflowRunStatus

        if config.success != WorkflowRunStatus.SUCCESS.value:
            return PublishValidationResult(
                can_publish=False,
                is_displayable=True,  # Can view failed experiments
                reason=(
                    "Cannot publish: Experiment did not complete successfully. "
                    f"Status: {config.success}"
                ),
            )

        # Check 6: Output directory exists (if checking disk)
        if check_files_on_disk:
            output_check = cls._check_output_files(workspace_id, unique_id)
            if output_check.get("error"):
                return PublishValidationResult(
                    can_publish=False,
                    is_displayable=False,
                    reason=output_check["error"],
                )

        # All checks passed
        return PublishValidationResult(
            can_publish=True,
            is_displayable=True,
        )

    @classmethod
    def _check_output_files(cls, workspace_id: str, unique_id: str) -> dict:
        """Check if experiment output directory exists."""
        experiment_dir = join_filepath([DIRPATH.OUTPUT_DIR, workspace_id, unique_id])

        if not os.path.exists(experiment_dir):
            return {"error": "Experiment output directory does not exist"}

        return {}

    @classmethod
    def validate_for_display(
        cls,
        workspace_id: str,
        unique_id: str,
    ) -> PublishValidationResult:
        """
        Simplified validation for display purposes only.
        Checks if experiment data can be displayed (regardless of publish status).
        """
        return cls.validate(
            workspace_id=workspace_id,
            unique_id=unique_id,
            user_has_s3_bucket=True,  # Not relevant for display
            check_files_on_disk=True,
        )


class DataviewService:
    DATAVIEW_PUBLIC_REQUEST_KEY = "DATAVIEW_PUBLIC_REQUEST"
    OUTPUTS_URL_PREFIX = r"^/outputs/[^/]+/"
    OUTPUTS_IMAGE_URL_PREFIX = r"^/outputs/image/"

    @classmethod
    def find_dataview_record(
        cls,
        db: Session,
        workspace_id: int,
        unique_id: str,
        published_only: bool = False,
    ) -> ExperimentRecord:
        """
        Search for a experiment_record that matches the specified id
        """
        query = (
            db.query(ExperimentRecord)
            .join(
                Workspace,
                Workspace.id == ExperimentRecord.workspace_id,
            )
            .filter(
                Workspace.deleted.is_(False),
                ExperimentRecord.workspace_id == int(workspace_id),
                ExperimentRecord.uid == unique_id,
            )
        )

        if published_only:
            query = query.filter(
                ExperimentRecord.publish_status == PublishStatus.on.value,
            )

        return query.first()

    @classmethod
    def find_published_dataview_record_input(
        cls, db: Session, workspace_id: int, input_path: str
    ) -> ExperimentRecord:
        """
        Search for an experiment_record that contains the specified input data
        """

        record: ExperimentRecord = (
            db.query(ExperimentRecord)
            .join(
                Workspace,
                Workspace.id == ExperimentRecord.workspace_id,
            )
            .filter(
                Workspace.deleted.is_(False),
                ExperimentRecord.workspace_id == int(workspace_id),
                ExperimentRecord.publish_status == PublishStatus.on.value,
                func.JSON_CONTAINS(
                    ExperimentRecord.input_paths, json.dumps(input_path)
                ),
                # > Note: input data does not depend on unique_id (shared within
                # >   a workspace), so it is determined by workspace_id only.
                # models.ExperimentRecord.uid == unique_id,
            )
            .first()
        )

        return record

    @classmethod
    def find_user_owned_dataview_record(
        cls, db: Session, record_id: int, user_id: int
    ) -> ExperimentRecord:
        """
        Search for the experiment_record
          that belongs to the specified record_id and user_id
        """

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
    def is_dataview_public_outputs_request(cls, req: Request) -> bool:
        """
        Check whether the access is to public output data (HTTP header check)
        """

        has_public_request_header = (
            cls.DATAVIEW_PUBLIC_REQUEST_KEY.lower() in req.headers
        )
        is_outputs_request = re.match(cls.OUTPUTS_URL_PREFIX, req.url.path)

        return has_public_request_header and is_outputs_request

    @classmethod
    def validate_dataview_public_outputs_request(
        cls, req: Request, db: Session
    ) -> bool:
        """
        Validate requests for public outputs data
        *Deny access to private outputs data
        """

        if not cls.is_dataview_public_outputs_request(req):
            return False

        request_url_path = req.url.path
        data_file_path = re.sub(cls.OUTPUTS_URL_PREFIX, "", request_url_path)

        is_allowed_access = False

        # Extract workspace_id and unique_id from path using centralized method
        ids = ExptOutputPathIds.from_request_url(
            request_url_path, cls.OUTPUTS_URL_PREFIX
        )
        workspace_id = ids.workspace_id
        unique_id = ids.unique_id

        # Request case for output data
        if workspace_id and unique_id:
            # Check whether the data is in a public record
            record = DataviewService.find_dataview_record(
                db, int(workspace_id), unique_id, published_only=True
            )
            is_allowed_access = record is not None

        # Request case for input data
        else:
            query_params = dict(req.query_params)
            workspace_id = query_params.get("workspace_id")

            # Check whether the data is in a public record
            record = cls.find_published_dataview_record_input(
                db, workspace_id, data_file_path
            )
            is_allowed_access = record is not None

        return is_allowed_access

    @classmethod
    def multiple_publish_dataview_records(
        cls,
        db: Session,
        user_id: int,
        ids: List[int],
        flag: PublishFlags,
    ):
        from studio.app.common.schemas.dataview import LocalSyncStatus

        # Build update dict with publish_status and local_sync_status
        update_dict = {ExperimentRecord.publish_status: int(flag == PublishFlags.on)}

        # Set sync status when publishing/unpublishing
        if flag == PublishFlags.on:
            update_dict[
                ExperimentRecord.local_sync_status
            ] = LocalSyncStatus.pending.value
        else:
            update_dict[
                ExperimentRecord.local_sync_status
            ] = LocalSyncStatus.synced.value

        db.query(ExperimentRecord).filter(
            Workspace.id == ExperimentRecord.workspace_id,
            User.id == Workspace.user_id,
            User.id == user_id,
            User.active.is_(True),
            ExperimentRecord.id.in_(ids),
        ).update(update_dict, synchronize_session=False)

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

    @staticmethod
    def select_best_thumbnail_input(
        workflow_config: WorkflowConfig,
    ) -> Tuple[Optional[str], DatasetPaths]:
        """Select the input node best suited for thumbnail generation.

        Priority: IMAGE (TIFF=3) > HDF5 with dataset path (2)
        > MAT with dataset path (1) > any other input node (0).

        This ensures that in multi-input workflows (e.g. tutorial4: HDF5
        imaging + MAT behavior) we always pick the most visually informative
        source regardless of dict order.

        Returns:
            (normalized_image_path, DatasetPaths)
        """
        input_node_types = [
            NodeType.IMAGE,
            NodeType.HDF5,
            NodeType.MATLAB,
            NodeType.MICROSCOPE,
            NodeType.CSV,
            NodeType.FLUO,
            NodeType.BEHAVIOR,
        ]
        best_priority = -1
        image_url = None
        dataset_paths = DatasetPaths()

        for _, node in workflow_config.nodeDict.items():
            if node.type not in input_node_types or not node.data.path:
                continue
            if node.type == NodeType.IMAGE:
                priority = 3
            elif node.type == NodeType.HDF5 and node.data.hdf5Path:
                priority = 2
            elif node.type == NodeType.MATLAB and node.data.matPath:
                priority = 1
            else:
                priority = 0
            if priority > best_priority:
                best_priority = priority
                image_url = normalize_output_path(node.data.path[0])
                dataset_paths = DatasetPaths(
                    hdf5_path=node.data.hdf5Path,
                    mat_path=node.data.matPath,
                )

        return image_url, dataset_paths

    @classmethod
    def make_dataview_thumnail_paths(
        cls,
        workspace_id: str,
        unique_id: str,
        experiment_config_: ExptConfig = None,
        workflow_config_: WorkflowConfig = None,
    ) -> Tuple[DataviewThumbnails, DatasetPaths]:
        """
        Create values to set in DataviewThumbnails
        *Constructed from ExptConfig and WorkflowConfig

        Returns:
            Tuple of (DataviewThumbnails, DatasetPaths)
        """

        # Make input data (image) thumbnails path (from WorkflowConfig)
        workflow_config = (
            workflow_config_
            if workflow_config_
            else WorkflowConfigReader.read(workspace_id, unique_id)
        )
        image_url, dataset_paths = cls.select_best_thumbnail_input(workflow_config)

        # Make output data (roi) thumbnails path (from ExptConfig)
        roi_url = None
        experiment_config = (
            experiment_config_
            if experiment_config_
            else ExptConfigReader.read(workspace_id, unique_id)
        )
        for _, function in experiment_config.function.items():
            if function.outputPaths and ("cell_roi" in function.outputPaths):
                roi_url = normalize_output_path(function.outputPaths["cell_roi"].path)
                break

        thumbnails = DataviewThumbnails(
            image_url=image_url,
            roi_url=roi_url,
        )
        return thumbnails, dataset_paths

    @classmethod
    def generate_thumbnail_images(
        cls,
        workspace_id: str,
        unique_id: str,
        image_path: Optional[str] = None,
        roi_path: Optional[str] = None,
        dataset_paths: Optional[DatasetPaths] = None,
    ) -> DataviewThumbnails:
        """
        Generate PNG thumbnails for DataView.

        Creates small PNG images from input files and ROI data for fast loading
        in DataView. These are ~50-100KB vs full data files which can be 100MB+.

        For TIFF files: renders first frame as grayscale image
        For HDF5/MAT files: renders dataset preview if dataset_paths provided
        For other formats (microscope, etc.): generates placeholder with file type label

        Stores in: {output_dir}/{workspace_id}/{unique_id}/thumbnails/
        - input_thumb.png (first frame of input TIFF or placeholder)
        - roi_thumb.png (rendered ROI overlay)

        Args:
            workspace_id: Workspace identifier
            unique_id: Experiment unique identifier
            image_path: Path to input file (TIFF, HDF5, MAT, etc.) (optional)
            roi_path: Path to cell_roi.json file (optional)
            dataset_paths: Internal dataset paths for structured data (optional)

        Returns:
            DataviewThumbnails with paths to generated PNG thumbnails
        """
        from studio.app.common.core.dataview.thumbnail_generator import (
            ThumbnailGenerator,
        )

        input_thumb_path = None
        roi_thumb_path = None

        # Generate input thumbnail
        if image_path:
            abs_image_path = ThumbnailGenerator.resolve_source_path(
                workspace_id, image_path
            )
            input_thumb_path = ThumbnailGenerator.get_thumbnail_path(
                workspace_id, unique_id, ThumbnailType.INPUT
            )
            try:
                create_directory(os.path.dirname(input_thumb_path))
                ThumbnailGenerator.generate_input_thumbnail(
                    source_path=image_path,
                    output_path=input_thumb_path,
                    abs_source_path=abs_image_path,
                    dataset_paths=dataset_paths,
                )
                logger.debug(f"Generated input thumbnail: {input_thumb_path}")
            except Exception as e:
                logger.warning(f"Failed to generate input thumbnail: {e}")
                input_thumb_path = None

        # Generate ROI thumbnail from cell_roi.json
        if roi_path:
            abs_roi_path = ThumbnailGenerator.resolve_source_path(
                workspace_id, roi_path
            )
            if abs_roi_path:
                roi_thumb_path = ThumbnailGenerator.get_thumbnail_path(
                    workspace_id, unique_id, ThumbnailType.ROI
                )
                try:
                    create_directory(os.path.dirname(roi_thumb_path))
                    ThumbnailGenerator.generate_roi_thumbnail(
                        abs_roi_path, roi_thumb_path
                    )
                    logger.debug(f"Generated ROI thumbnail: {roi_thumb_path}")
                except Exception as e:
                    logger.warning(f"Failed to generate ROI thumbnail: {e}")
                    roi_thumb_path = None

        return DataviewThumbnails(
            image_url=(
                normalize_output_path(input_thumb_path) if input_thumb_path else None
            ),
            roi_url=normalize_output_path(roi_thumb_path) if roi_thumb_path else None,
        )
