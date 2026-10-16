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
    save_memmap_c_order,
    save_memmap_c_order_fallback,
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

        mc.motion_correct(save_movie=True)
        border_to_0 = 0 if mc.border_nan == "copy" else mc.border_to_0

        # memory mapping
        mmap_file_new = save_memmap_c_order(
            mc.mmap_file, base_name=function_id, border_to_0=border_to_0
        )

    # now load the file
    Yr, dims, T = load_memmap(mmap_file_new)

    # The corrected movie stays memory-mapped: this view of the pixels x time
    # file has the layout the full in-memory copy (np.array of it) used to have,
    # so the mean below is the same arithmetic on the same values
    movie = Yr.T.reshape((T,) + dims, order="F")

    meanImg, rois = __process_images(movie)

    # A frame of the pixels x time file is spread over all of it, so the TIFF
    # is written from MotionCorrect's frame-contiguous file when the C-order
    # file is known to be that file plus CaImAn's 0.0001 offset
    if save_memmap_c_order_fallback(mc.mmap_file, border_to_0) is None:
        Yf, _, _ = load_memmap(mc.mmap_file[0])
        frames, offset = Yf.T.reshape((T,) + dims, order="F"), C_ORDER_OFFSET
    else:
        # caiman.save_memmap has just held the movie in memory, so a copy costs
        # no more than that did, where batches of the pixels x time view would
        # re-read the whole file each
        Yf, frames, offset = None, np.array(movie), None

    xy_trans_data = (
        (np.array(mc.x_shifts_els), np.array(mc.y_shifts_els))
        if opts.get("motion", "pw_rigid")
        else np.array(mc.shifts_rig)
    )

    mc_images = ImageData(
        movie_to_tiff(frames, output_dir, "mc_images", offset=offset),
        output_dir=output_dir,
        file_name="mc_images",
    )

    # Release variables associated with memmap files when they are no longer needed.
    # *Avoid lock errors when cleaning memmap files.
    del movie, frames, Yf, Yr, dims, T

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


# What CaImAn 1.11's save_memmap adds to each motion-corrected value
C_ORDER_OFFSET = (0.0001, 0.0)
# tifffile.imwrite switches to BigTIFF above this many bytes (its `bigsize`)
BIGTIFF_BYTES = 2**32 - 2**25
# frames per write, bounded in bytes
TIFF_BATCH_BYTES = 64 << 20


def movie_to_tiff(
    movie, output_dir, file_name, offset=None, batch_bytes=TIFF_BATCH_BYTES
):
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
    frame_bytes = int(np.prod(movie.shape[1:])) * dtype.itemsize
    batch_size = max(1, batch_bytes // frame_bytes)

    def frames():
        for i in range(0, movie.shape[0], batch_size):
            batch = np.ascontiguousarray(movie[i : i + batch_size], dtype=dtype)
            for value in offset or ():
                batch = batch + np.float32(value)
            yield from batch

    try:
        tifffile.imwrite(
            tmp_path,
            frames(),
            shape=movie.shape,
            dtype=dtype,
            bigtiff=movie.shape[0] * frame_bytes > BIGTIFF_BYTES,
        )
        os.replace(tmp_path, tiff_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
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
