import asyncio
import math
import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from functools import partial
from typing import Callable, Tuple, TypeVar

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
from studio.app.common.core.utils.executor_utils import run_in_executor_with_context
from studio.app.common.core.workflow.workflow_result import await_despite_cancel
from studio.app.optinist.core.edit_ROI.wrappers import edit_roi_algos
from studio.app.optinist.schemas.roi import RoiPos

logger = AppLogger.get_logger()

_T = TypeVar("_T")

# ROI edits, data filters and a commit's taking of the experiment lock, one at a
# time per process, as on the event loop: an edit and a filter of a node do not
# interleave, an edit under way finishes before a commit takes the lock, one
# queued behind the commit finds it taken, and one node's data is held at a time
_edit_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="roi-edit")


class EditRoiUtils:
    @classmethod
    def get_algo(cls, filepath):
        algo = next((algo for algo in edit_roi_algos if algo in filepath), None)
        if not algo:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
        return algo

    @classmethod
    async def run_on_edit_thread(cls, func: Callable[[], _T]) -> _T:
        """func() on the ROI edit thread, with the request's context."""
        return await run_in_executor_with_context(_edit_executor, func)

    @classmethod
    async def execute(cls, filepath: str, remote_bucket_name: str):
        cls.get_algo(filepath)
        client_id = get_client_id_for_subprocess()

        # Get workspace_id, unique_id from output file path
        ids = ExptOutputPathIds(os.path.dirname(filepath))
        workspace_id = ids.workspace_id
        unique_id = ids.unique_id

        # The commit rewrites the node pickle, its JSON and whole.nwb in place, so
        # the experiment takes the lock whether or not remote storage is enabled.
        # - If lock file exists, an exception is raised (raise_error=True)
        def take_lock() -> str:
            RemoteSyncLockFileUtil.check_sync_lock_file(
                workspace_id, unique_id, raise_error=True
            )
            return RemoteSyncLockFileUtil.create_sync_lock_file(workspace_id, unique_id)

        lock_taken = asyncio.ensure_future(cls.run_on_edit_thread(take_lock))
        try:
            token = await await_despite_cancel(lock_taken)
        except asyncio.CancelledError:
            # The thread took the lock anyway, and no commit follows to release it
            if lock_taken.exception() is None:
                RemoteSyncLockFileUtil.delete_sync_lock_file(
                    workspace_id, unique_id, lock_taken.result()
                )
            raise

        try:
            # Operate remote storage data.
            if RemoteStorageController.is_available():
                # creating remote_sync_status file.
                # - The status file is used to pass bucket info to subsequent
                #   processing.
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
                    executor,
                    partial(cls._execute_process, filepath, client_id=client_id),
                )

                logger.info("finish edit_roi commit process.")
        finally:
            # The one release, after the upload; the token keeps it from taking
            # a lock another request created once this one expired.
            RemoteSyncLockFileUtil.delete_sync_lock_file(workspace_id, unique_id, token)

    @classmethod
    @with_client_id_context  # Automatically set client_id for logging
    def _execute_process(cls, filepath: str, client_id: str = None) -> None:
        from studio.app.optinist.core.edit_ROI.edit_ROI import EditROI

        asyncio.run(EditROI(file_path=filepath).commit())


def ellipse_pixels(shape: Tuple[int, int], roi_pos: RoiPos) -> np.ndarray:
    """Flat indices of the ellipse at column posx, row posy, sizex x sizey."""
    x, y, width, height = (
        round(roi_pos.posx),
        round(roi_pos.posy),
        round(roi_pos.sizex),
        round(roi_pos.sizey),
    )

    if width < 1 or height < 1:
        return np.zeros(0, np.int64)
    a = width / 2
    b = height / 2

    # only the ellipse's bounding box, clipped to the frame, can hold its pixels
    rows = np.arange(max(0, math.floor(y - b)), min(shape[0], math.ceil(y + b) + 1))
    cols = np.arange(max(0, math.floor(x - a)), min(shape[1], math.ceil(x + a) + 1))
    yy, xx = np.meshgrid(rows, cols, indexing="ij")

    # Calculate the distance of each pixel from the center of the ellipse
    distance = ((xx - x) / a) ** 2 + ((yy - y) / b) ** 2
    inside = distance <= 1

    return np.ravel_multi_index((yy[inside], xx[inside]), shape)
