import os
from glob import glob
from typing import Optional

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends

from studio.app.common.core.auth.auth_dependencies import get_user_remote_bucket_name
from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.snakemake.smk_utils import SmkUtils
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    RemoteStorageReader,
    RemoteSyncStatusFileUtil,
)
from studio.app.common.core.storage.s3_storage_controller import S3StorageController
from studio.app.common.core.utils.file_reader import JsonReader, Reader
from studio.app.common.core.utils.filepath_creater import (
    create_directory,
    join_filepath,
)
from studio.app.common.core.utils.json_writer import JsonWriter, save_tiff2json
from studio.app.common.core.workspace.workspace_dependencies import (
    is_workspace_available,
)
from studio.app.common.schemas.outputs import JsonTimeSeriesData, OutputData
from studio.app.const import ACCEPT_FILE_EXT, ORIGINAL_DATA_EXT
from studio.app.dir_path import DIRPATH

router = APIRouter(prefix="/outputs", tags=["outputs"])

logger = AppLogger.get_logger()


async def _background_full_sync(
    remote_bucket_name: str, workspace_id: str, unique_id: str
) -> None:
    """
    Background task to download remaining experiment files (PKL, NWB) after
    visualization files have been loaded. This prepares the experiment for
    Edit ROI without blocking the user.
    """
    try:
        # Check if full sync is still needed
        is_unsynced = RemoteSyncStatusFileUtil.check_sync_status_unsynced(
            workspace_id, unique_id
        )

        if not is_unsynced:
            logger.debug(
                f"Background sync skipped - already synced: {workspace_id}/{unique_id}"
            )
            return

        logger.info(f"Background full sync starting for {workspace_id}/{unique_id}")

        async with RemoteStorageReader(
            remote_bucket_name, workspace_id, unique_id
        ) as remote_storage_controller:
            await remote_storage_controller.download_experiment(
                workspace_id, unique_id, sync_mode="all"
            )

        logger.info(f"Background full sync completed for {workspace_id}/{unique_id}")

    except Exception as e:
        # Log but don't raise - this is a background task
        logger.warning(
            f"Background full sync failed for {workspace_id}/{unique_id}: {e}"
        )


@router.post(
    "/sync/{workspace_id}/{unique_id}",
    response_model=bool,
    dependencies=[Depends(is_workspace_available)],
    description="""
    Sync visualization files (JSON, TIFF) from S3 for viewing experiment results.
    Call this before loading visualization data to ensure files are available locally.
    Only syncs files needed for visualization, not large PKL/NWB files.
    Automatically triggers background sync for remaining files (PKL/NWB) for Edit ROI.
    """,
)
async def sync_visualization_files(
    workspace_id: str,
    unique_id: str,
    background_tasks: BackgroundTasks,
    remote_bucket_name: str = Depends(get_user_remote_bucket_name),
) -> bool:
    """
    Lazy-load visualization files from S3.
    Downloads only JSON and TIFF files needed for viewing results.
    Then triggers background download of PKL/NWB files for Edit ROI.
    """
    if not RemoteStorageController.is_available():
        return True  # No remote storage, files should be local

    # Check if sync is needed
    is_unsynced = RemoteSyncStatusFileUtil.check_sync_status_unsynced(
        workspace_id, unique_id
    )

    if not is_unsynced:
        return True  # Already fully synced

    logger.info(f"Syncing visualization files for {workspace_id}/{unique_id} from S3")

    async with RemoteStorageReader(
        remote_bucket_name, workspace_id, unique_id
    ) as remote_storage_controller:
        result = await remote_storage_controller.download_experiment(
            workspace_id, unique_id, sync_mode="visualization"
        )

        # Also download input files needed for viewing images
        input_filenames = SmkUtils.get_datatypes_inputs(
            workspace_id, unique_id, apply_basename=True
        )
        for input_filename in input_filenames:
            await remote_storage_controller.download_input_data(
                workspace_id, input_filename
            )

    # Trigger background task to download remaining files (PKL/NWB)
    # This prepares Edit ROI while user is viewing results
    background_tasks.add_task(
        _background_full_sync, remote_bucket_name, workspace_id, unique_id
    )

    return result


async def _ensure_visualization_synced(dirpath: str, remote_bucket_name: str) -> None:
    """
    On-demand sync for visualization files.
    Extracts workspace_id and unique_id from dirpath and triggers sync if needed.
    """
    if not RemoteStorageController.is_available():
        return

    if not dirpath.startswith(DIRPATH.OUTPUT_DIR):
        return

    # Extract IDs from path
    path_ids = ExptOutputPathIds(dirpath)
    workspace_id = path_ids.workspace_id
    unique_id = path_ids.unique_id

    if not workspace_id or not unique_id:
        return

    # Check if sync is needed
    is_unsynced = RemoteSyncStatusFileUtil.check_sync_status_unsynced(
        workspace_id, unique_id
    )

    if not is_unsynced:
        return

    logger.info(f"On-demand sync for visualization: {workspace_id}/{unique_id}")

    async with RemoteStorageReader(
        remote_bucket_name, workspace_id, unique_id
    ) as remote_storage_controller:
        await remote_storage_controller.download_experiment(
            workspace_id, unique_id, sync_mode="visualization"
        )
        # Also download input files
        input_filenames = SmkUtils.get_datatypes_inputs(
            workspace_id, unique_id, apply_basename=True
        )
        for input_filename in input_filenames:
            await remote_storage_controller.download_input_data(
                workspace_id, input_filename
            )


def get_initial_timeseries_data(dirpath) -> JsonTimeSeriesData:
    plot_meta_path = f"{dirpath}.plot-meta.json"
    plot_meta = JsonReader.read_as_plot_meta(plot_meta_path)

    return JsonTimeSeriesData(
        xrange=[],
        data={},
        std={},
        meta=plot_meta,
    )


@router.get("/inittimedata/{dirpath:path}", response_model=JsonTimeSeriesData)
async def get_inittimedata(
    dirpath: str,
    isFull: Optional[bool] = None,
    remote_bucket_name: str = Depends(get_user_remote_bucket_name),
):
    # On-demand sync if files don't exist
    await _ensure_visualization_synced(dirpath, remote_bucket_name)

    full_json_dirpath = dirpath + ORIGINAL_DATA_EXT
    if isFull and os.path.exists(full_json_dirpath):
        dirpath = full_json_dirpath

    file_numbers = sorted(
        [
            os.path.splitext(os.path.basename(x))[0]
            for x in glob(join_filepath([dirpath, "*.json"]))
        ]
    )

    # Handle empty case
    if not file_numbers:
        return_data = get_initial_timeseries_data(dirpath)
        return_data.meta = {"title": "0 ROIs found"}  # Set informative message
        return return_data

    # Rest of the function remains the same
    index = file_numbers[0]
    str_index = str(index)

    json_data = JsonReader.read_as_timeseries(
        join_filepath([dirpath, f"{str(index)}.json"])
    )

    data = {
        str(i): {json_data.xrange[0]: json_data.data[json_data.xrange[0]]}
        for i in file_numbers
    }

    if json_data.std is not None:
        std = {
            str(i): {json_data.xrange[0]: json_data.data[json_data.xrange[0]]}
            for i in file_numbers
        }

    return_data = get_initial_timeseries_data(dirpath)
    return_data.xrange = json_data.xrange
    if json_data.std is not None:
        return_data.std = std

    return_data.data = data
    return_data.data[str_index] = json_data.data
    if json_data.std is not None:
        return_data.std[str_index] = json_data.std

    return return_data


@router.get("/timedata/{dirpath:path}", response_model=JsonTimeSeriesData)
async def get_timedata(
    dirpath: str,
    index: int,
    isFull: Optional[bool] = None,
    remote_bucket_name: str = Depends(get_user_remote_bucket_name),
):
    # On-demand sync if files don't exist
    await _ensure_visualization_synced(dirpath, remote_bucket_name)

    full_json_dirpath = dirpath + ORIGINAL_DATA_EXT
    if isFull and os.path.exists(full_json_dirpath):
        dirpath = full_json_dirpath

    json_data = JsonReader.read_as_timeseries(
        join_filepath([dirpath, f"{str(index)}.json"])
    )

    return_data = get_initial_timeseries_data(dirpath)

    str_index = str(index)
    return_data.data[str_index] = json_data.data
    if json_data.std is not None:
        return_data.std[str_index] = json_data.std

    return return_data


@router.get("/alltimedata/{dirpath:path}", response_model=JsonTimeSeriesData)
async def get_alltimedata(
    dirpath: str,
    remote_bucket_name: str = Depends(get_user_remote_bucket_name),
):
    # On-demand sync if files don't exist
    await _ensure_visualization_synced(dirpath, remote_bucket_name)

    return_data = get_initial_timeseries_data(dirpath)

    for i, path in enumerate(glob(join_filepath([dirpath, "*.json"]))):
        str_idx = str(os.path.splitext(os.path.basename(path))[0])
        json_data = JsonReader.read_as_timeseries(path)
        if i == 0:
            return_data.xrange = json_data.xrange

        return_data.data[str_idx] = json_data.data
        if json_data.std is not None:
            return_data.std[str_idx] = json_data.std

    return return_data


@router.get("/data/{filepath:path}", response_model=OutputData)
async def get_file(
    filepath: str,
    remote_bucket_name: str = Depends(get_user_remote_bucket_name),
):
    # On-demand sync if files don't exist
    await _ensure_visualization_synced(os.path.dirname(filepath), remote_bucket_name)

    return JsonReader.read_as_output(filepath)


@router.get("/html/{filepath:path}", response_model=OutputData)
async def get_html(filepath: str):
    return Reader.read_as_output(filepath)


@router.get("/image/{filepath:path}", response_model=OutputData)
async def get_image(
    filepath: str,
    workspace_id: str,
    start_index: Optional[int] = 0,
    end_index: Optional[int] = 10,
    isFull: Optional[bool] = None,
    remote_bucket_name: str = Depends(get_user_remote_bucket_name),
):
    # On-demand sync if files don't exist
    await _ensure_visualization_synced(os.path.dirname(filepath), remote_bucket_name)

    filename, ext = os.path.splitext(os.path.basename(filepath))

    if filename == "cell_roi" and isFull:
        full_cell_roi_filepath = filepath + ORIGINAL_DATA_EXT
        if os.path.exists(full_cell_roi_filepath):
            filepath = full_cell_roi_filepath

    if ext in ACCEPT_FILE_EXT.TIFF_EXT.value:
        if not filepath.startswith(join_filepath([DIRPATH.OUTPUT_DIR, workspace_id])):
            filepath = join_filepath([DIRPATH.INPUT_DIR, workspace_id, filepath])

            # On-demand sync for input files
            if not os.path.exists(filepath) and RemoteStorageController.is_available():
                logger.info(f"On-demand sync for input file: {workspace_id}/{filename}")
                s3_controller = S3StorageController(remote_bucket_name)
                await s3_controller.download_input_data(workspace_id, filename + ext)

        save_dirpath = join_filepath(
            [
                os.path.dirname(filepath),
                filename,
            ]
        )
        json_filepath = join_filepath(
            [save_dirpath, f"{filename}_{str(start_index)}_{str(end_index)}.json"]
        )
        if not os.path.exists(json_filepath):
            save_tiff2json(filepath, save_dirpath, start_index, end_index)
    else:
        json_filepath = filepath

    return JsonReader.read_as_output(json_filepath)


@router.get("/csv/{filepath:path}", response_model=OutputData)
async def get_csv(
    filepath: str,
    workspace_id: str,
    remote_bucket_name: str = Depends(get_user_remote_bucket_name),
):
    original_filename = os.path.basename(filepath)
    filepath = join_filepath([DIRPATH.INPUT_DIR, workspace_id, filepath])

    # On-demand sync for input files
    if not os.path.exists(filepath) and RemoteStorageController.is_available():
        logger.info(f"On-demand sync for input: {workspace_id}/{original_filename}")
        s3_controller = S3StorageController(remote_bucket_name)
        await s3_controller.download_input_data(workspace_id, original_filename)

    filename, _ = os.path.splitext(os.path.basename(filepath))
    save_dirpath = join_filepath([os.path.dirname(filepath), filename])
    create_directory(save_dirpath)
    json_filepath = join_filepath([save_dirpath, f"{filename}.json"])

    JsonWriter.write_as_split(json_filepath, pd.read_csv(filepath, header=None))
    return JsonReader.read_as_output(json_filepath)
