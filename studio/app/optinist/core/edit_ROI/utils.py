import asyncio
import os
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from typing import Tuple

import numpy as np
from fastapi import HTTPException, status

from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.logger_context_helpers import (
    get_client_id_for_subprocess,
    with_client_id_context,
)
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    RemoteSyncAction,
    RemoteSyncLockFileUtil,
    RemoteSyncStatusFileUtil,
)
from studio.app.optinist.core.edit_ROI.wrappers import edit_roi_algos
from studio.app.optinist.schemas.roi import RoiPos

logger = AppLogger.get_logger()


class EditRoiUtils:
    @classmethod
    def get_algo(cls, filepath):
        algo = next((algo for algo in edit_roi_algos if algo in filepath), None)
        if not algo:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
        return algo

    @classmethod
    async def execute(cls, filepath: str, remote_bucket_name: str):
        cls.get_algo(filepath)
        client_id = get_client_id_for_subprocess()

        # Get workspace_id, unique_id from output file path
        ids = ExptOutputPathIds(os.path.dirname(filepath))
        workspace_id = ids.workspace_id
        unique_id = ids.unique_id

        # Operate remote storage data.
        if RemoteStorageController.is_available():
            # Check for remote-sync-lock-file
            # - If lock file exists, an exception is raised (raise_error=True)
            RemoteSyncLockFileUtil.check_sync_lock_file(
                workspace_id, unique_id, raise_error=True
            )

            # creating remote-sync-lock-file
            RemoteSyncLockFileUtil.create_sync_lock_file(workspace_id, unique_id)

            # creating remote_sync_status file.
            # - The status file is used to pass bucket info to subsequent processing.
            RemoteSyncStatusFileUtil.create_sync_status_file_for_processing(
                remote_bucket_name,
                workspace_id,
                unique_id,
                RemoteSyncAction.UPLOAD,
            )

        # Commit in a worker process: the node pickle carries the whole movie,
        # and the event loop must stay responsive meanwhile.
        with ProcessPoolExecutor(max_workers=1) as executor:
            logger.info("start edit_roi commit process.")

            await asyncio.get_running_loop().run_in_executor(
                executor, partial(cls._execute_process, filepath, client_id=client_id)
            )

            logger.info("finish edit_roi commit process.")

    @classmethod
    @with_client_id_context  # Automatically set client_id for logging
    def _execute_process(cls, filepath: str, client_id: str = None) -> None:
        from studio.app.optinist.core.edit_ROI.edit_ROI import EditROI

        asyncio.run(EditROI(file_path=filepath).commit())


def create_ellipse_mask(shape: Tuple[int, int], roi_pos: RoiPos):
    x, y, width, height = (
        round(roi_pos.posx),
        round(roi_pos.posy),
        round(roi_pos.sizex),
        round(roi_pos.sizey),
    )

    x_coords = np.arange(0, shape[0])
    y_coords = np.arange(0, shape[1])
    xx, yy = np.meshgrid(x_coords, y_coords)

    # Calculate the distance of each pixel from the center of the ellipse
    a = width / 2
    b = height / 2
    distance = ((xx - x) / a) ** 2 + ((yy - y) / b) ** 2

    # Set the pixels within the ellipse to 1 and the pixels outside to NaN
    ellipse = np.empty(shape)
    ellipse[:] = np.nan
    ellipse[distance <= 1] = 1

    return ellipse
