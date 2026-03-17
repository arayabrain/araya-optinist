import os
from typing import Optional

import h5py
import numpy as np
import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import FileResponse

from studio.app.common.core.auth.auth_dependencies import get_outputs_remote_bucket_name
from studio.app.common.core.dataview.thumbnail_generator import ThumbnailGenerator
from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.snakemake.smk_utils import SmkUtils
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteExperimentNotFoundError,
    RemoteExperimentSyncMode,
    RemoteStorageController,
    RemoteStorageLockError,
    RemoteStorageReader,
    RemoteStorageSimpleReader,
    RemoteStorageSimpleWriter,
    RemoteSyncStatusFileUtil,
)
from studio.app.common.core.utils.file_reader import JsonReader, Reader
from studio.app.common.core.utils.filepath_creater import (
    create_directory,
    join_filepath,
    normalize_output_path,
)
from studio.app.common.core.utils.json_writer import JsonWriter, save_tiff2json
from studio.app.common.core.workflow.workflow_reader import WorkflowConfigReader
from studio.app.common.core.workspace.workspace_dependencies import (
    is_workspace_available,
)
from studio.app.common.dataclass.timeseries_chunk_handler import TimeSeriesChunkHandler
from studio.app.common.schemas.outputs import JsonTimeSeriesData, OutputData
from studio.app.const import ACCEPT_FILE_EXT, ORIGINAL_DATA_EXT, ThumbnailType
from studio.app.dir_path import DIRPATH
from studio.app.optinist.routers.mat import MatGetter

router = APIRouter(prefix="/outputs", tags=["outputs"])

logger = AppLogger.get_logger()


async def get_or_generate_thumbnail(
    workspace_id: str,
    unique_id: str,
    original_path: str,
    remote_bucket_name: str,
    thumb_type: ThumbnailType,
    hdf5_path: str = None,
    mat_path: str = None,
) -> str:
    """
    Get thumbnail path, generating if needed (lazy migration).

    For backward compatibility with experiments that don't have PNG thumbnails:
    1. Check if PNG thumbnail exists → return it
    2. If not, check if original file exists locally
       - If not, download from remote storage
    3. Generate PNG from the original file
    4. Upload PNG to remote storage for future use
    5. Return PNG path

    Args:
        workspace_id: Workspace identifier
        unique_id: Experiment unique identifier
        original_path: Path to original TIFF or JSON file
        remote_bucket_name: remote storage bucket name for remote storage
        thumb_type: ThumbnailType.INPUT (for TIFF) or ThumbnailType.ROI
            (for cell_roi.json)
        hdf5_path: Internal HDF5 dataset path (optional)
        mat_path: Internal MAT dataset path (optional)

    Returns:
        Path to the thumbnail PNG file (may be newly generated)
    """
    thumb_path = ThumbnailGenerator.get_thumbnail_path(
        workspace_id, unique_id, thumb_type
    )

    # Check if PNG thumbnail already exists
    if os.path.exists(thumb_path):
        return normalize_output_path(thumb_path)

    # Resolve the original file path
    abs_original_path = ThumbnailGenerator.resolve_source_path(
        workspace_id, original_path
    )

    # Download from remote storage if needed
    if abs_original_path is None and RemoteStorageController.is_available():
        async with RemoteStorageReader(
            remote_bucket_name,
            workspace_id,
            unique_id,
            RemoteExperimentSyncMode.THUMBNAILS_ONLY,
        ) as remote_storage_controller:
            await remote_storage_controller.download_thumbnail_source(
                workspace_id, unique_id, original_path, thumb_type
            )
        # Re-resolve after download
        abs_original_path = ThumbnailGenerator.resolve_source_path(
            workspace_id, original_path
        )

    # Generate thumbnail
    # - INPUT: always generates (TIFF render if file exists, placeholder if not)
    # - ROI: requires the source file to exist
    can_generate = False
    if thumb_type == ThumbnailType.INPUT:
        can_generate = True  # generate_input_thumbnail handles missing files
    elif abs_original_path is not None:
        can_generate = True

    if can_generate:
        try:
            create_directory(os.path.dirname(thumb_path))

            if thumb_type == ThumbnailType.INPUT:
                ThumbnailGenerator.generate_input_thumbnail(
                    source_path=original_path,
                    output_path=thumb_path,
                    abs_source_path=abs_original_path,
                    hdf5_path=hdf5_path,
                    mat_path=mat_path,
                )
            else:
                ThumbnailGenerator.generate_roi_thumbnail(abs_original_path, thumb_path)

            logger.info(f"Lazy-generated thumbnail: {thumb_path}")

            # Upload to remote storage for future use (fire and forget)
            if RemoteStorageController.is_available():
                try:
                    async with RemoteStorageSimpleWriter(
                        remote_bucket_name
                    ) as remote_storage_controller:
                        await remote_storage_controller.upload_thumbnail(
                            workspace_id, unique_id, thumb_path
                        )
                except Exception as e:
                    logger.warning(
                        f"Failed to upload generated thumbnail to remote storage: {e}"
                    )

            return normalize_output_path(thumb_path)

        except Exception as e:
            logger.warning(f"Failed to generate thumbnail: {e}")

    # Fall back to original path if all else fails
    return normalize_output_path(original_path)


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

        sync_mode = RemoteExperimentSyncMode.ALL
        async with RemoteStorageReader(
            remote_bucket_name, workspace_id, unique_id, sync_mode
        ) as remote_storage_controller:
            await remote_storage_controller.download_experiment(
                workspace_id, unique_id, sync_mode=sync_mode
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
    Sync visualization files (JSON, TIFF)
    from remote storage for viewing experiment results.
    Call this before loading visualization data to ensure files are available locally.
    Only syncs files needed for visualization, not large PKL/NWB files.
    Automatically triggers background sync for remaining files (PKL/NWB) for Edit ROI.
    """,
)
async def sync_visualization_files(
    workspace_id: str,
    unique_id: str,
    background_tasks: BackgroundTasks,
    remote_bucket_name: str = Depends(get_outputs_remote_bucket_name),
) -> bool:
    """
    Lazy-load visualization files from remote storage.
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

    logger.info(
        f"Syncing visualization files for {workspace_id}/{unique_id} "
        "from remote storage"
    )

    try:
        sync_mode = RemoteExperimentSyncMode.VISUALIZATION
        async with RemoteStorageReader(
            remote_bucket_name, workspace_id, unique_id, sync_mode
        ) as remote_storage_controller:
            result = await remote_storage_controller.download_experiment(
                workspace_id,
                unique_id,
                sync_mode=sync_mode,
            )

            # Also download input files needed for viewing images
            try:
                input_filenames = SmkUtils.get_datatypes_inputs(
                    workspace_id, unique_id, apply_basename=True
                )
                for input_filename in input_filenames:
                    await remote_storage_controller.download_input_data(
                        workspace_id, input_filename
                    )
            except (AssertionError, KeyError):
                # snakemake.yaml may be empty or missing required keys
                pass
    except RemoteExperimentNotFoundError as e:
        logger.warning(e)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except RemoteStorageLockError as e:
        logger.warning(e)
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=str(e))

    # Trigger background task to download remaining files (PKL/NWB)
    # This prepares Edit ROI while user is viewing results
    background_tasks.add_task(
        _background_full_sync, remote_bucket_name, workspace_id, unique_id
    )

    return result


@router.get(
    "/thumbnail/{workspace_id}/{unique_id}/{thumb_type}",
    description="""
    Get a thumbnail PNG image for an experiment.
    Syncs from remote storage if not available locally,
      or generates on-demand if needed.

    Args:
        workspace_id: Workspace identifier
        unique_id: Experiment unique identifier
        thumb_type: Either "input" or "roi"

    Returns:
        PNG image file
    """,
)
async def get_thumbnail(
    workspace_id: str,
    unique_id: str,
    thumb_type: ThumbnailType,
    remote_bucket_name: str = Depends(get_outputs_remote_bucket_name),
):
    """
    Serve thumbnail PNG images for DataView.

    This endpoint handles:
    1. Syncing thumbnails from remote storage if not available locally
    2. Generating thumbnails on-demand from source files if needed
    3. Serving the PNG file with proper content type
    """

    # Get the expected thumbnail path
    thumb_path = ThumbnailGenerator.get_thumbnail_path(
        workspace_id, unique_id, thumb_type
    )

    # Try to sync thumbnail from remote storage if not available locally
    if not os.path.exists(thumb_path) and RemoteStorageController.is_available():
        try:
            sync_mode = RemoteExperimentSyncMode.THUMBNAILS_ONLY
            async with RemoteStorageReader(
                remote_bucket_name, workspace_id, unique_id, sync_mode
            ) as remote_storage_controller:
                await remote_storage_controller.download_experiment(
                    workspace_id,
                    unique_id,
                    sync_mode=sync_mode,
                )
        except RemoteExperimentNotFoundError as e:
            logger.warning(e)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        except RemoteStorageLockError as e:
            logger.warning(e)
            raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=str(e))
        except Exception as e:
            logger.warning(f"Failed to sync thumbnail from remote storage: {e}")
            pass  # Continue processing

    # If thumbnail still doesn't exist, try to generate it
    if not os.path.exists(thumb_path):
        # Sync essential config files (yaml) so we can determine source paths
        # Note: thumbnails_only mode doesn't download the config files needed to
        # find the source TIFF/JSON files for generation
        if RemoteStorageController.is_available():
            try:
                sync_mode = RemoteExperimentSyncMode.ESSENTIAL_ONLY
                async with RemoteStorageReader(
                    remote_bucket_name, workspace_id, unique_id, sync_mode
                ) as remote_storage_controller:
                    await remote_storage_controller.download_experiment(
                        workspace_id,
                        unique_id,
                        sync_mode=sync_mode,
                    )
            except RemoteExperimentNotFoundError as e:
                logger.warning(e)
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
                )
            except RemoteStorageLockError as e:
                logger.warning(e)
                raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=str(e))
            except Exception as e:
                logger.warning(f"Failed to sync config files from remote storage: {e}")
                pass  # Continue processing

        # Get the original file path for generation
        hdf5_path = None
        mat_path = None
        if thumb_type == ThumbnailType.INPUT:
            # Need to find the input file and dataset paths
            try:
                input_filenames = SmkUtils.get_datatypes_inputs(
                    workspace_id, unique_id, apply_basename=True
                )
                if input_filenames:
                    original_path = input_filenames[0]
                else:
                    raise HTTPException(
                        status_code=404, detail="No input files found for thumbnail"
                    )
            except (AssertionError, KeyError):
                raise HTTPException(
                    status_code=404, detail="Could not determine input file"
                )

            # Extract hdf5Path/matPath from workflow config, preferring the
            # node that will produce the best thumbnail (HDF5 > MAT).
            try:
                from studio.app.common.core.workflow.workflow import NodeType

                wf_config = WorkflowConfigReader.read(workspace_id, unique_id)
                best_priority = -1
                for _, node in wf_config.nodeDict.items():
                    if not node.data.path:
                        continue
                    if node.type == NodeType.HDF5 and node.data.hdf5Path:
                        priority = 2
                    elif node.type == NodeType.MATLAB and node.data.matPath:
                        priority = 1
                    else:
                        continue
                    if priority > best_priority:
                        best_priority = priority
                        hdf5_path = node.data.hdf5Path
                        mat_path = node.data.matPath
            except Exception:
                pass  # Dataset paths are optional enhancement
        else:
            # ROI thumbnail uses cell_roi.json - need to find actual path from config
            from studio.app.common.core.dataview.dataview_services import (
                DataviewService,
            )

            try:
                thumbnails, _, _ = DataviewService.make_dataview_thumnail_paths(
                    workspace_id, unique_id
                )
                if thumbnails.roi_url:
                    original_path = thumbnails.roi_url
                else:
                    raise HTTPException(
                        status_code=404, detail="No ROI data found for thumbnail"
                    )
            except Exception as e:
                logger.warning(f"Could not determine ROI path: {e}")
                raise HTTPException(
                    status_code=404, detail="Could not determine ROI file path"
                )

        # Generate thumbnail (may download source from remote storage if needed)
        # Note: get_or_generate_thumbnail returns a normalized (relative) path
        await get_or_generate_thumbnail(
            workspace_id,
            unique_id,
            original_path,
            remote_bucket_name,
            thumb_type,
            hdf5_path=hdf5_path,
            mat_path=mat_path,
        )
        # Re-get the absolute path since generation should have created the file
        thumb_path = ThumbnailGenerator.get_thumbnail_path(
            workspace_id, unique_id, thumb_type
        )

    # Check if thumbnail exists after all attempts
    if not os.path.exists(thumb_path):
        raise HTTPException(
            status_code=404,
            detail=f"Thumbnail not available: {thumb_type.filename}",
        )

    return FileResponse(
        thumb_path,
        media_type="image/png",
        filename=thumb_type.filename,
    )


async def _ensure_visualization_synced(dirpath: str, remote_bucket_name: str) -> None:
    """
    On-demand sync for visualization files.
    Extracts workspace_id and unique_id from dirpath and triggers sync if needed.
    """
    if not RemoteStorageController.is_available():
        return

    if not dirpath.startswith(DIRPATH.OUTPUT_DIR):
        return

    # Trim path to workspace_id/unique_id level
    # (ExptOutputPathIds expects 2-3 components)
    relative_path = os.path.relpath(dirpath, DIRPATH.OUTPUT_DIR)
    path_parts = relative_path.split(os.sep)
    if len(path_parts) < 2:
        return
    trimmed_path = os.path.join(DIRPATH.OUTPUT_DIR, *path_parts[:2])

    # Extract IDs from path
    path_ids = ExptOutputPathIds(trimmed_path)
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

    try:
        sync_mode = RemoteExperimentSyncMode.VISUALIZATION
        async with RemoteStorageReader(
            remote_bucket_name, workspace_id, unique_id, sync_mode
        ) as remote_storage_controller:
            await remote_storage_controller.download_experiment(
                workspace_id,
                unique_id,
                sync_mode=sync_mode,
            )
            # Also download input files (if snakemake config is available)
            try:
                input_filenames = SmkUtils.get_datatypes_inputs(
                    workspace_id, unique_id, apply_basename=True
                )
                for input_filename in input_filenames:
                    await remote_storage_controller.download_input_data(
                        workspace_id, input_filename
                    )
            except (AssertionError, KeyError):
                # snakemake.yaml may be empty or missing required keys
                pass
    except RemoteExperimentNotFoundError as e:
        logger.warning(e)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except RemoteStorageLockError as e:
        logger.warning(e)
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=str(e))


def get_initial_timeseries_data(dirpath) -> JsonTimeSeriesData:
    plot_meta_path = f"{dirpath}.plot-meta.json"
    plot_meta = JsonReader.read_as_plot_meta(plot_meta_path)

    return JsonTimeSeriesData(
        xrange=[],
        data={},
        std={},
        meta=plot_meta,
    )


def _load_timeseries_record(dirpath: str, record_id: str) -> JsonTimeSeriesData:
    """
    Load a single timeseries record from either chunked or legacy format.

    Args:
        dirpath: Directory containing the timeseries data
        record_id: Record identifier (as string)

    Returns:
        JsonTimeSeriesData for the specified record
    """
    if TimeSeriesChunkHandler.is_chunked_format(dirpath):
        # Chunked format
        cell_data = TimeSeriesChunkHandler.get_record_data(dirpath, record_id)
        # Convert from split format to DataFrame
        df = pd.DataFrame(
            cell_data["data"], index=cell_data["index"], columns=cell_data["columns"]
        )
        return JsonReader.read_as_timeseries_from_df(df)
    else:
        # Legacy format
        return JsonReader.read_as_timeseries(
            join_filepath([dirpath, f"{record_id}.json"])
        )


@router.get("/inittimedata/{dirpath:path}", response_model=JsonTimeSeriesData)
async def get_inittimedata(
    dirpath: str,
    isFull: Optional[bool] = None,
    remote_bucket_name: str = Depends(get_outputs_remote_bucket_name),
):
    # Normalize and convert to absolute path for filesystem operations
    dirpath = normalize_output_path(dirpath)
    abs_dirpath = join_filepath([DIRPATH.OUTPUT_DIR, dirpath])

    # On-demand sync if files don't exist
    await _ensure_visualization_synced(abs_dirpath, remote_bucket_name)

    full_json_dirpath = abs_dirpath + ORIGINAL_DATA_EXT
    if isFull and os.path.exists(full_json_dirpath):
        abs_dirpath = full_json_dirpath

    file_numbers = TimeSeriesChunkHandler.get_all_record_ids(abs_dirpath)

    # Handle empty case
    if not file_numbers:
        return_data = get_initial_timeseries_data(abs_dirpath)
        return_data.meta = {"title": "0 ROIs found"}  # Set informative message
        return return_data

    # Get first cell data
    index = file_numbers[0]
    str_index = str(index)

    # Load first record using common helper
    json_data = _load_timeseries_record(abs_dirpath, str_index)

    data = {
        str(i): {json_data.xrange[0]: json_data.data[json_data.xrange[0]]}
        for i in file_numbers
    }

    if json_data.std is not None:
        std = {
            str(i): {json_data.xrange[0]: json_data.data[json_data.xrange[0]]}
            for i in file_numbers
        }

    return_data = get_initial_timeseries_data(abs_dirpath)
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
    remote_bucket_name: str = Depends(get_outputs_remote_bucket_name),
):
    # Normalize and convert to absolute path for filesystem operations
    dirpath = normalize_output_path(dirpath)
    abs_dirpath = join_filepath([DIRPATH.OUTPUT_DIR, dirpath])

    # On-demand sync if files don't exist
    await _ensure_visualization_synced(abs_dirpath, remote_bucket_name)

    full_json_dirpath = abs_dirpath + ORIGINAL_DATA_EXT
    if isFull and os.path.exists(full_json_dirpath):
        abs_dirpath = full_json_dirpath

    str_index = str(index)

    # Load record using common helper
    json_data = _load_timeseries_record(abs_dirpath, str_index)

    return_data = get_initial_timeseries_data(abs_dirpath)

    return_data.data[str_index] = json_data.data
    if json_data.std is not None:
        return_data.std[str_index] = json_data.std

    return return_data


@router.get("/alltimedata/{dirpath:path}", response_model=JsonTimeSeriesData)
async def get_alltimedata(
    dirpath: str,
    remote_bucket_name: str = Depends(get_outputs_remote_bucket_name),
):
    # Normalize and convert to absolute path for filesystem operations
    dirpath = normalize_output_path(dirpath)
    abs_dirpath = join_filepath([DIRPATH.OUTPUT_DIR, dirpath])

    # On-demand sync if files don't exist
    await _ensure_visualization_synced(abs_dirpath, remote_bucket_name)

    return_data = get_initial_timeseries_data(abs_dirpath)

    if TimeSeriesChunkHandler.is_chunked_format(abs_dirpath):
        # Chunked format: load all chunks
        all_records = TimeSeriesChunkHandler.load_all_records(abs_dirpath)

        for cell_index, cell_data in all_records.items():
            # Convert from split format to timeseries format
            df = pd.DataFrame(
                cell_data["data"],
                index=cell_data["index"],
                columns=cell_data["columns"],
            )
            json_data = JsonReader.read_as_timeseries_from_df(df)

            if not return_data.xrange:
                return_data.xrange = json_data.xrange

            return_data.data[cell_index] = json_data.data
            if json_data.std is not None:
                if not return_data.std:
                    return_data.std = {}
                return_data.std[cell_index] = json_data.std
    else:
        # Legacy format: individual files
        from glob import glob

        metadata_files = [
            TimeSeriesChunkHandler.INDEX_MAP_FILENAME,  # chunk_index_map.json
            f"{os.path.basename(dirpath)}.plot-meta.json",
        ]
        for i, path in enumerate(glob(join_filepath([abs_dirpath, "*.json"]))):
            filename = os.path.basename(path)
            # Skip metadata files
            if filename in metadata_files:
                continue

            str_idx = str(os.path.splitext(filename)[0])
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
    remote_bucket_name: str = Depends(get_outputs_remote_bucket_name),
):
    # Normalize and convert to absolute path for filesystem operations
    filepath = normalize_output_path(filepath)
    abs_filepath = join_filepath([DIRPATH.OUTPUT_DIR, filepath])

    # On-demand sync if files don't exist
    await _ensure_visualization_synced(
        os.path.dirname(abs_filepath), remote_bucket_name
    )

    return JsonReader.read_as_output(abs_filepath)


@router.get("/html/{filepath:path}", response_model=OutputData)
async def get_html(filepath: str):
    # Normalize and convert to absolute path for filesystem operations
    filepath = normalize_output_path(filepath)
    abs_filepath = join_filepath([DIRPATH.OUTPUT_DIR, filepath])
    return Reader.read_as_output(abs_filepath)


@router.get("/image/{filepath:path}", response_model=OutputData)
async def get_image(
    filepath: str,
    workspace_id: str,
    unique_id: Optional[str] = None,  # For published data access validation
    start_index: Optional[int] = 0,
    end_index: Optional[int] = 10,
    isFull: Optional[bool] = None,
    remote_bucket_name: str = Depends(get_outputs_remote_bucket_name),
):
    # Normalize filepath for backward compatibility with existing DB records
    # that may contain absolute paths like /app/studio_data/output/...
    filepath = normalize_output_path(filepath)

    # Convert to absolute path for filesystem operations
    abs_filepath = join_filepath([DIRPATH.OUTPUT_DIR, filepath])

    # On-demand sync if files don't exist
    await _ensure_visualization_synced(
        os.path.dirname(abs_filepath), remote_bucket_name
    )

    filename, ext = os.path.splitext(os.path.basename(filepath))

    if filename == "cell_roi" and isFull:
        full_cell_roi_filepath = abs_filepath + ORIGINAL_DATA_EXT
        if os.path.exists(full_cell_roi_filepath):
            abs_filepath = full_cell_roi_filepath

    if ext in ACCEPT_FILE_EXT.TIFF_EXT.value:
        # Check if this is an input file (just filename)
        # vs output file (has workspace path)
        is_input_file = not filepath.startswith(f"{workspace_id}/")
        if is_input_file:
            abs_filepath = join_filepath([DIRPATH.INPUT_DIR, workspace_id, filepath])

            # On-demand sync for input files
            if (
                not os.path.exists(abs_filepath)
                and RemoteStorageController.is_available()
            ):
                logger.info(f"On-demand sync for input file: {workspace_id}/{filename}")
                async with RemoteStorageSimpleReader(
                    remote_bucket_name
                ) as remote_storage_controller:
                    await remote_storage_controller.download_input_data(
                        workspace_id, filename + ext
                    )

            # Return 404 if file still doesn't exist after sync attempt
            if not os.path.exists(abs_filepath):
                raise HTTPException(
                    status_code=404,
                    detail=f"Input file not found: {filename}{ext}",
                )

        save_dirpath = join_filepath(
            [
                os.path.dirname(abs_filepath),
                filename,
            ]
        )
        json_filepath = join_filepath(
            [save_dirpath, f"{filename}_{str(start_index)}_{str(end_index)}.json"]
        )
        if not os.path.exists(json_filepath):
            save_tiff2json(abs_filepath, save_dirpath, start_index, end_index)
    else:
        json_filepath = abs_filepath
        # Check if output file exists after sync attempt
        if not os.path.exists(json_filepath):
            if remote_bucket_name:
                logger.warning(f"File not found after sync attempt: {json_filepath}")
                # Distinguish between "syncing in progress" vs "file doesn't exist"
                # If the experiment directory exists but the file doesn't, the file
                # likely doesn't exist in remote storage either (analysis incomplete)
                experiment_dir = os.path.dirname(os.path.dirname(json_filepath))
                if os.path.exists(experiment_dir):
                    # Experiment dir exists but file missing -
                    # likely analysis incomplete
                    raise HTTPException(
                        status_code=404,
                        detail="Output file not found. "
                        "Analysis may not have generated this file.",
                    )
                else:
                    # Experiment dir doesn't exist - sync may still be in progress
                    raise HTTPException(
                        status_code=503,
                        detail="Data syncing. Please retry.",
                    )
            raise HTTPException(
                status_code=404,
                detail="Output file not found",
            )

    return JsonReader.read_as_output(json_filepath)


@router.get("/csv/{filepath:path}", response_model=OutputData)
async def get_csv(
    filepath: str,
    workspace_id: str,
    remote_bucket_name: str = Depends(get_outputs_remote_bucket_name),
):
    original_filename = os.path.basename(filepath)
    filepath = join_filepath([DIRPATH.INPUT_DIR, workspace_id, filepath])

    # On-demand sync for input files
    if not os.path.exists(filepath) and RemoteStorageController.is_available():
        logger.info(f"On-demand sync for input: {workspace_id}/{original_filename}")
        async with RemoteStorageSimpleReader(
            remote_bucket_name
        ) as remote_storage_controller:
            await remote_storage_controller.download_input_data(
                workspace_id, original_filename
            )

    filename, _ = os.path.splitext(os.path.basename(filepath))
    save_dirpath = join_filepath([os.path.dirname(filepath), filename])
    create_directory(save_dirpath)
    json_filepath = join_filepath([save_dirpath, f"{filename}.json"])

    JsonWriter.write_as_split(json_filepath, pd.read_csv(filepath, header=None))
    return JsonReader.read_as_output(json_filepath)


@router.get("/structured/{workspace_id}/{unique_id}/{node_id}")
async def get_structured_data(
    workspace_id: str,
    unique_id: str,
    node_id: str,
    start_index: Optional[int] = 0,
    end_index: Optional[int] = 10,
):
    try:
        config = WorkflowConfigReader.read(workspace_id, unique_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Workflow config not found")

    node = config.nodeDict.get(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")

    file_path = node.data.path
    if isinstance(file_path, list):
        file_path = file_path[0] if file_path else None
    if not file_path:
        raise HTTPException(status_code=400, detail="Node has no file path")

    full_path = join_filepath([DIRPATH.INPUT_DIR, workspace_id, file_path])
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    hdf5_path = node.data.hdf5Path
    mat_path = node.data.matPath

    try:
        if hdf5_path is not None:
            with h5py.File(full_path, "r") as f:
                dataset = f[hdf5_path]
                shape = dataset.shape
                ndim = dataset.ndim
                if ndim == 3:
                    si = max(0, start_index)
                    ei = min(shape[0], end_index)
                    data = dataset[si:ei]
                else:
                    data = dataset[:]
        elif mat_path is not None:
            raw = MatGetter.data(full_path, mat_path)
            data = np.asarray(raw)
            shape = data.shape
            ndim = data.ndim
            if ndim == 3:
                si = max(0, start_index)
                ei = min(shape[0], end_index)
                data = data[si:ei]
        else:
            raise HTTPException(
                status_code=400,
                detail="Node has no hdf5Path or matPath",
            )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"Dataset not found: {e}")

    data = np.asarray(data)
    dataset_path = hdf5_path or mat_path

    match ndim:
        case 3:
            return {
                "data": data.tolist(),
                "data_type": "images",
                "total_frames": int(shape[0]),
                "dataset_path": dataset_path,
            }
        case 2:
            df = pd.DataFrame(data)
            return {
                "data": df.to_dict(orient="split")["data"],
                "columns": [str(c) for c in df.columns.tolist()],
                "index": [str(i) for i in df.index.tolist()],
                "data_type": "timeseries",
                "dataset_path": dataset_path,
            }
        case 1:
            return {
                "data": data.tolist(),
                "index": list(range(len(data))),
                "data_type": "bar",
                "dataset_path": dataset_path,
            }
        case _:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported data dimensionality: {ndim}",
            )
