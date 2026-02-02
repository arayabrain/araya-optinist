import json
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

import imageio.v3 as imageio
import numpy as np
import tifffile
from fastapi import Request
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
    def find_published_dataview_record(
        cls, db: Session, workspace_id: int, unique_id: str
    ) -> ExperimentRecord:
        """
        Search for a published experiment_record that matches the specified id
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
                ExperimentRecord.uid == unique_id,
                ExperimentRecord.publish_status == PublishStatus.on.value,
            )
            .first()
        )

        return record

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
                ExperimentRecord.thumbnails["image_url"].as_string() == input_path,
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

        # Try to extract IDs from output path pattern
        ids = ExptOutputPathIds(data_file_path)

        # Request case for output data
        if ids.workspace_id:
            # Check whether the data is in a public record
            record = DataviewService.find_published_dataview_record(
                db, int(ids.workspace_id), ids.unique_id
            )
            is_allowed_access = record is not None

        # Request case for input data
        else:
            ids = None
            query_params = dict(req.query_params)
            workspace_id = query_params.get("workspace_id")

            # For image data
            if re.match(cls.OUTPUTS_IMAGE_URL_PREFIX, request_url_path):
                # Check whether the data is in a public record
                record = cls.find_published_dataview_record_input(
                    db, workspace_id, data_file_path
                )
                is_allowed_access = record is not None

            # For other data
            else:
                """
                Currently (2025-9), this feature does not support validation
                  of data other than images.
                - This is because information about input data other than images is not
                  stored in experiment_records (a specification change is required).
                - While validation will need to be strengthened in the future,
                  the initial version will only validate images
                  (since most preview requests are images).
                """

                # Force Allow Access
                is_allowed_access = True

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
                image_url = normalize_output_path(node.data.path[0])
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
                roi_url = normalize_output_path(function.outputPaths["cell_roi"].path)
                break

        return DataviewThumbnails(
            image_url=image_url,
            roi_url=roi_url,
        )

    @classmethod
    def generate_thumbnail_images(
        cls,
        workspace_id: str,
        unique_id: str,
        image_path: Optional[str] = None,
        roi_path: Optional[str] = None,
    ) -> DataviewThumbnails:
        """
        Generate PNG thumbnails for DataView.

        Creates small PNG images from input TIFF and ROI data for fast loading
        in DataView. These are ~50-100KB vs full TIFFs which can be 100MB+.

        Stores in: {output_dir}/{workspace_id}/{unique_id}/thumbnails/
        - input_thumb.png (first frame of input TIFF)
        - roi_thumb.png (rendered ROI overlay)

        Args:
            workspace_id: Workspace identifier
            unique_id: Experiment unique identifier
            image_path: Path to input TIFF file (optional)
            roi_path: Path to cell_roi.json file (optional)

        Returns:
            DataviewThumbnails with paths to generated PNG thumbnails
        """
        thumb_dir = join_filepath(
            [DIRPATH.OUTPUT_DIR, workspace_id, unique_id, "thumbnails"]
        )

        input_thumb_path = None
        roi_thumb_path = None

        # Generate input thumbnail from TIFF
        if image_path:
            abs_image_path = cls._resolve_image_path(workspace_id, image_path)
            if abs_image_path and os.path.exists(abs_image_path):
                try:
                    create_directory(thumb_dir)
                    input_thumb_path = join_filepath([thumb_dir, "input_thumb.png"])
                    cls._generate_tiff_thumbnail(abs_image_path, input_thumb_path)
                    logger.info(f"Generated input thumbnail: {input_thumb_path}")
                except Exception as e:
                    logger.warning(f"Failed to generate input thumbnail: {e}")
                    input_thumb_path = None

        # Generate ROI thumbnail from cell_roi.json
        if roi_path:
            abs_roi_path = roi_path
            if not os.path.isabs(roi_path):
                abs_roi_path = join_filepath([DIRPATH.OUTPUT_DIR, roi_path])
            if os.path.exists(abs_roi_path):
                try:
                    create_directory(thumb_dir)
                    roi_thumb_path = join_filepath([thumb_dir, "roi_thumb.png"])
                    cls._generate_roi_thumbnail(abs_roi_path, roi_thumb_path)
                    logger.info(f"Generated ROI thumbnail: {roi_thumb_path}")
                except Exception as e:
                    logger.warning(f"Failed to generate ROI thumbnail: {e}")
                    roi_thumb_path = None

        return DataviewThumbnails(
            image_url=normalize_output_path(input_thumb_path)
            if input_thumb_path
            else None,
            roi_url=normalize_output_path(roi_thumb_path) if roi_thumb_path else None,
        )

    @classmethod
    def _resolve_image_path(cls, workspace_id: str, image_path: str) -> Optional[str]:
        """
        Resolve image path to absolute path.
        Input images can be in the input directory (just filename) or output directory.
        """
        if os.path.isabs(image_path) and os.path.exists(image_path):
            return image_path

        # Try as relative path from output dir
        abs_path = join_filepath([DIRPATH.OUTPUT_DIR, image_path])
        if os.path.exists(abs_path):
            return abs_path

        # Try as input file (just filename)
        filename = os.path.basename(image_path)
        input_path = join_filepath([DIRPATH.INPUT_DIR, workspace_id, filename])
        if os.path.exists(input_path):
            return input_path

        return None

    @classmethod
    def _generate_tiff_thumbnail(
        cls, tiff_path: str, output_path: str, max_size: int = 512
    ) -> None:
        """
        Generate a PNG thumbnail from the first frame of a TIFF file.

        Args:
            tiff_path: Path to source TIFF file
            output_path: Path to save PNG thumbnail
            max_size: Maximum dimension for thumbnail (default 512px)
        """
        # Read only the first frame to minimize memory usage
        img = tifffile.imread(tiff_path, key=0)

        # Handle multi-channel images (take first channel or average)
        if img.ndim > 2:
            img = img[..., 0] if img.shape[-1] <= 4 else img[0]

        # Normalize to uint8
        img_float = img.astype(np.float32)
        img_min, img_max = img_float.min(), img_float.max()
        if img_max > img_min:
            img_normalized = ((img_float - img_min) / (img_max - img_min) * 255).astype(
                np.uint8
            )
        else:
            # Uniform image: set to mid-gray (128) for visibility
            img_normalized = np.full_like(img, 128, dtype=np.uint8)

        # Resize if larger than max_size while preserving aspect ratio
        h, w = img_normalized.shape[:2]
        if max(h, w) > max_size:
            scale = max_size / max(h, w)
            new_h, new_w = int(h * scale), int(w * scale)
            # Simple resize using slicing (nearest neighbor)
            y_indices = (np.arange(new_h) * h / new_h).astype(int)
            x_indices = (np.arange(new_w) * w / new_w).astype(int)
            img_normalized = img_normalized[np.ix_(y_indices, x_indices)]

        # Save as PNG
        imageio.imwrite(output_path, img_normalized)

    @classmethod
    def _generate_roi_thumbnail(
        cls, roi_json_path: str, output_path: str, size: Tuple[int, int] = (512, 512)
    ) -> None:
        """
        Generate a PNG thumbnail from ROI data (cell_roi.json).

        Creates a colored image showing ROI outlines/masks.

        Args:
            roi_json_path: Path to cell_roi.json file
            output_path: Path to save PNG thumbnail
            size: Output image size (width, height)
        """
        with open(roi_json_path) as f:
            roi_data = json.load(f)

        # Initialize blank image (RGB)
        img = np.zeros((size[1], size[0], 3), dtype=np.uint8)

        # Get all ROIs and determine bounding box
        all_x = []
        all_y = []
        rois = []

        for key, value in roi_data.items():
            if isinstance(value, dict) and "x" in value and "y" in value:
                x_coords = value["x"]
                y_coords = value["y"]
                if x_coords and y_coords:
                    all_x.extend(x_coords)
                    all_y.extend(y_coords)
                    rois.append((x_coords, y_coords))

        if not rois:
            # No ROIs found, save blank image
            imageio.imwrite(output_path, img)
            return

        # Calculate scaling to fit ROIs in output image
        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)
        roi_width = max_x - min_x
        roi_height = max_y - min_y

        if roi_width == 0 or roi_height == 0:
            imageio.imwrite(output_path, img)
            return

        # Add padding (10%)
        padding = 0.1
        scale_x = size[0] * (1 - 2 * padding) / roi_width
        scale_y = size[1] * (1 - 2 * padding) / roi_height
        scale = min(scale_x, scale_y)

        offset_x = size[0] * padding - min_x * scale
        offset_y = size[1] * padding - min_y * scale

        # Generate colors for each ROI
        np.random.seed(42)  # Consistent colors
        colors = np.random.randint(100, 255, size=(len(rois), 3), dtype=np.uint8)

        # Draw each ROI
        for idx, (x_coords, y_coords) in enumerate(rois):
            color = tuple(int(c) for c in colors[idx])
            # Scale and offset coordinates
            scaled_x = [int(x * scale + offset_x) for x in x_coords]
            scaled_y = [int(y * scale + offset_y) for y in y_coords]

            # Draw polygon outline
            for i in range(len(scaled_x)):
                x1, y1 = scaled_x[i], scaled_y[i]
                x2, y2 = (
                    scaled_x[(i + 1) % len(scaled_x)],
                    scaled_y[(i + 1) % len(scaled_y)],
                )
                cls._draw_line(img, x1, y1, x2, y2, color)

        imageio.imwrite(output_path, img)

    @staticmethod
    def _draw_line(
        img: np.ndarray,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        color: Tuple[int, int, int],
    ) -> None:
        """Draw a line on an image using Bresenham's algorithm."""
        h, w = img.shape[:2]

        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        sx = 1 if x1 < x2 else -1
        sy = 1 if y1 < y2 else -1
        err = dx - dy

        while True:
            if 0 <= x1 < w and 0 <= y1 < h:
                img[y1, x1] = color

            if x1 == x2 and y1 == y2:
                break

            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x1 += sx
            if e2 < dx:
                err += dx
                y1 += sy
