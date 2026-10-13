import os

from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.rules.benchmark_recorder import BenchmarkRecorder
from studio.app.common.dataclass import ImageData
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import RoiData
from studio.app.optinist.wrappers.caiman.caiman_utils import (
    save_memmap_c_order,
    CaimanUtils,
    caiman_cluster,
    distribute_params_to_groups,
)
from studio.app.optinist.wrappers.optinist.utils import recursive_flatten_params

logger = AppLogger.get_logger()


def caiman_mc(
    image: ImageData, output_dir: str, params: dict = None, **kwargs
) -> dict(mc_images=ImageData):
    import numpy as np
    from caiman import load_memmap
    from caiman.motion_correction import MotionCorrect
    from caiman.source_extraction.cnmf.params import CNMFParams

    function_id = ExptOutputPathIds(output_dir).function_id
    unique_id = ExptOutputPathIds(output_dir).unique_id
    logger.info(f"start caiman motion_correction: {function_id}")

    flattened_params = {}
    recursive_flatten_params(params or {}, flattened_params)
    params = flattened_params

    # Specify a unique CAIMAN_TEMPDIR
    # *To avoid collisions of temporary files (memmap files)
    mc_unique_id = f"{unique_id}_{function_id}"
    CaimanUtils.set_caimam_byid_tempdir(mc_unique_id)

    opts = CNMFParams()

    if params:
        opts.change_params(params_dict=distribute_params_to_groups(params, vars(opts)))

    with caiman_cluster() as (dview, n_processes):
        mc = MotionCorrect(image.path, dview=dview, **opts.get_group("motion"))

        with BenchmarkRecorder.phase("mc.motion_correct"):
            mc.motion_correct(save_movie=True)
        border_to_0 = 0 if mc.border_nan == "copy" else mc.border_to_0

        # memory mapping
        with BenchmarkRecorder.phase("mc.save_memmap"):
            mmap_file_new = save_memmap_c_order(
                mc.mmap_file, base_name=function_id, border_to_0=border_to_0
            )

    # now load the file
    Yr, dims, T = load_memmap(mmap_file_new)

    # The corrected movie stays memory-mapped: this view of the pixels x time
    # file has the layout the full in-memory copy (np.array of it) used to have,
    # so the mean below is the same arithmetic on the same values
    movie = Yr.T.reshape((T,) + dims, order="F")

    with BenchmarkRecorder.phase("mc.process_images"):
        meanImg, rois = __process_images(movie)

    xy_trans_data = (
        (np.array(mc.x_shifts_els), np.array(mc.y_shifts_els))
        if opts.get("motion", "pw_rigid")
        else np.array(mc.shifts_rig)
    )

    with BenchmarkRecorder.phase("mc.write_tiff"):
        mc_images = ImageData(
            movie_to_tiff(movie, output_dir, "mc_images"),
            output_dir=output_dir,
            file_name="mc_images",
        )

    # Release variables associated with memmap files when they are no longer needed.
    # *Avoid lock errors when cleaning memmap files.
    del movie, Yr, dims, T

    nwbfile = {}
    nwbfile[NWBDATASET.MOTION_CORRECTION] = {
        function_id: {
            "mc_data": mc_images,
            "xy_trans_data": xy_trans_data,
        }
    }

    info = {
        "mc_images": mc_images,
        "meanImg": ImageData(meanImg, output_dir=output_dir, file_name="meanImg"),
        "rois": RoiData(rois, output_dir=output_dir, file_name="rois"),
        "nwbfile": nwbfile,
    }

    # Clean up temporary files
    try:
        __handle_mmap_cleanup(mc, mmap_file_new)
    except Exception as e:
        logger.warning("caiman_mc: Failed to cleanup memmap files.")
        logger.warning(e)

    # Clean up unique CAIMAN_TEMPDIR
    try:
        CaimanUtils.cleanup_caiman_byid_tempdir(mc_unique_id)
    except Exception as e:
        logger.warning("caiman_mc: Failed to cleanup tempdir.")
        logger.warning(e)

    return info


def movie_to_tiff(movie, output_dir, file_name, batch_size=200):
    """
    Write a (T, Ly, Lx) array-like to the TIFF path ImageData would use,
    batch_size frames at a time. tifffile writes a frame iterator with a
    declared shape exactly as it writes the whole array (a byte-identical
    file), so a memory-mapped movie is never copied into memory as a whole.
    """
    import numpy as np
    import tifffile

    from studio.app.common.core.utils.filepath_creater import (
        create_directory,
        join_filepath,
    )

    tiff_dir = join_filepath([output_dir, "tiff", file_name])
    create_directory(tiff_dir)
    tiff_path = join_filepath([tiff_dir, f"{file_name}.tif"])

    def frames():
        for i in range(0, movie.shape[0], batch_size):
            yield from np.ascontiguousarray(movie[i : i + batch_size])

    tifffile.imwrite(tiff_path, frames(), shape=movie.shape, dtype=movie.dtype)
    return [tiff_path]


def __process_images(images):
    import numpy as np
    from caiman.base.rois import extract_binary_masks_from_structural_channel

    meanImg = images.mean(axis=0)
    rois = (
        extract_binary_masks_from_structural_channel(
            meanImg, gSig=7, expand_method="dilation"
        )[0]
        .reshape(meanImg.shape[0], meanImg.shape[1], -1)
        .transpose(2, 0, 1)
    )

    rois = rois.astype(np.float32)
    for i, _ in enumerate(rois):
        rois[i] *= i + 1

    rois = np.nanmax(rois, axis=0)
    rois[rois == 0] = np.nan
    rois -= 1
    return meanImg, rois


def __handle_mmap_cleanup(mc, mmap_file_new):
    # Explicitly gc before deleting memmap file
    # *Avoid lock errors when cleaning memmap files.
    import gc

    gc.collect()

    for mmap_file in mc.mmap_file:
        if os.path.isfile(mmap_file):
            os.remove(mmap_file)

    if os.path.isfile(mmap_file_new):
        os.remove(mmap_file_new)
