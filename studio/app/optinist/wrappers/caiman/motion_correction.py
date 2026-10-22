import os

from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.dataclass import ImageData
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import RoiData
from studio.app.optinist.wrappers.caiman.caiman_utils import (
    CaimanUtils,
    caiman_cluster,
    distribute_params_to_groups,
    needs_save_memmap,
)
from studio.app.optinist.wrappers.optinist.utils import recursive_flatten_params

logger = AppLogger.get_logger()


def caiman_mc(
    image: ImageData, output_dir: str, params: dict = None, **kwargs
) -> dict(mc_images=ImageData, meanImg=ImageData, refImg=ImageData):
    import numpy as np
    from caiman import load_memmap, save_memmap
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

        mc.motion_correct(save_movie=True)
        border_to_0 = 0 if mc.border_nan == "copy" else mc.border_to_0

        reason = needs_save_memmap(mc.mmap_file, border_to_0)
        if reason:
            logger.warning(
                "caiman.save_memmap converts the movie in memory: %s", reason
            )
            mmap_file_new = save_memmap(
                mc.mmap_file, base_name=function_id, order="C", border_to_0=border_to_0
            )
        else:
            mmap_file_new = None

    if mmap_file_new is None:
        # MotionCorrect's file plus C_ORDER_OFFSET is what save_memmap would write
        Yr, dims, T = load_memmap(mc.mmap_file[0])
        offset = C_ORDER_OFFSET
        meanImg = mean_over_time(Yr, offset).reshape(dims, order="F")
        frames = Yr.T.reshape((T,) + dims, order="F")
    else:
        Yr, dims, T = load_memmap(mmap_file_new)
        offset = None
        frames = np.array(Yr.T.reshape((T,) + dims, order="F"))
        meanImg = frames.mean(axis=0)

    rois = __extract_rois(meanImg)

    pw_rigid = opts.get("motion", "pw_rigid")
    xy_trans_data = (
        (np.array(mc.x_shifts_els), np.array(mc.y_shifts_els))
        if pw_rigid
        else np.array(mc.shifts_rig)
    )
    refImg = mc.total_template_els if pw_rigid else mc.total_template_rig

    mc_images = ImageData(
        movie_to_tiff(frames, output_dir, "mc_images", offset=offset),
        output_dir=output_dir,
        file_name="mc_images",
    )

    # Release variables associated with memmap files when they are no longer needed.
    # *Avoid lock errors when cleaning memmap files.
    del frames, Yr, dims, T

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
        "refImg": ImageData(refImg, output_dir=output_dir, file_name="refImg"),
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


# What CaImAn 1.11's save_memmap adds to each motion-corrected value
C_ORDER_OFFSET = (0.0001, 0.0)
# tifffile.imwrite switches to BigTIFF above this many bytes (its `bigsize`)
BIGTIFF_BYTES = 2**32 - 2**25
# rows per batch, bounded in bytes
BATCH_BYTES = 64 << 20


def batches(array, dtype, offset=None, batch_bytes=BATCH_BYTES):
    """
    C-contiguous copies of consecutive runs of array[i] of at most batch_bytes
    each, as dtype, with each value of offset added in float32 in that order,
    as save_memmap adds them.
    """
    import numpy as np

    row_bytes = int(np.prod(array.shape[1:])) * dtype.itemsize
    size = max(1, batch_bytes // row_bytes)
    for i in range(0, array.shape[0], size):
        batch = np.array(array[i : i + size], dtype=dtype, order="C")
        for value in offset or ():
            batch += np.float32(value)
        yield batch


def mean_over_time(Yr, offset, batch_bytes=BATCH_BYTES):
    """
    The mean over time of each pixel of a pixels x time movie plus offset, in
    float32. Each batch of pixels is reduced along its contiguous time axis, as
    the movie save_memmap writes in C order is, so the values are bit-identical.
    """
    import numpy as np

    dtype = np.dtype(np.float32)
    return np.concatenate(
        [batch.mean(axis=1) for batch in batches(Yr, dtype, offset, batch_bytes)]
    )


def movie_to_tiff(movie, output_dir, file_name, offset=None, batch_bytes=BATCH_BYTES):
    """
    Write a (T, Ly, Lx) array-like to the TIFF path ImageData would use, one
    batch of at most batch_bytes at a time, so a memory-mapped movie is never
    copied into memory as a whole. The file is byte-identical to writing the
    whole array: tifffile picks BigTIFF from data.nbytes, which a frame
    iterator does not have, so the choice is made here the same way.

    offset, when given, is a tuple of values added in float32 in that order,
    as save_memmap adds them. The file is written beside the final path and
    moved there when complete, so a failed write leaves no partial TIFF.
    """
    import os

    import numpy as np
    import tifffile

    from studio.app.common.core.utils.filepath_creater import (
        create_directory,
        join_filepath,
    )

    tiff_dir = join_filepath([output_dir, "tiff", file_name])
    create_directory(tiff_dir)
    tiff_path = join_filepath([tiff_dir, f"{file_name}.tif"])
    tmp_path = f"{tiff_path}.partial"

    dtype = np.dtype(np.float32 if offset else movie.dtype)
    frames = (
        frame for batch in batches(movie, dtype, offset, batch_bytes) for frame in batch
    )

    try:
        tifffile.imwrite(
            tmp_path,
            frames,
            shape=movie.shape,
            dtype=dtype,
            bigtiff=int(np.prod(movie.shape)) * dtype.itemsize > BIGTIFF_BYTES,
        )
        os.replace(tmp_path, tiff_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    return [tiff_path]


def __extract_rois(meanImg):
    import numpy as np
    from caiman.base.rois import extract_binary_masks_from_structural_channel

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
    return rois


def __handle_mmap_cleanup(mc, mmap_file_new):
    # Explicitly gc before deleting memmap file
    # *Avoid lock errors when cleaning memmap files.
    import gc

    gc.collect()

    for mmap_file in mc.mmap_file:
        if os.path.isfile(mmap_file):
            os.remove(mmap_file)

    if mmap_file_new and os.path.isfile(mmap_file_new):
        os.remove(mmap_file_new)
