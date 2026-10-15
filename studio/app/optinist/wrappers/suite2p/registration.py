from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.dataclass import ImageData
from studio.app.optinist.dataclass import Suite2pData
from studio.app.optinist.wrappers.optinist.utils import recursive_flatten_params

logger = AppLogger.get_logger()


def suite2p_registration(
    ops: Suite2pData, output_dir: str, params: dict = None, **kwargs
) -> dict(ops=Suite2pData, mc_images=ImageData):
    from suite2p import default_ops, io, registration

    function_id = ExptOutputPathIds(output_dir).function_id
    logger.info("start suite2p registration: %s", function_id)

    flattened_params = {}
    recursive_flatten_params(params, flattened_params)
    params = flattened_params

    ops = ops.data
    refImg = ops["meanImg"]

    # REGISTRATION
    if len(refImg.shape) == 3:
        refImg = refImg[0]

    # This node's batch_size sizes registration only (its peak memory grows
    # with it); downstream nodes keep the batch_size they were given.
    input_batch_size = ops.get("batch_size")
    ops = {**default_ops(), **ops, **params}

    # register binary
    ops = registration.register_binary(ops, refImg=refImg)

    # compute metrics for registration (QC only; off by default)
    if ops.get("do_regmetrics", True) and ops["nframes"] >= 1500:
        ops = registration.get_pc_metrics(ops)

    if input_batch_size is not None:
        ops["batch_size"] = input_batch_size

    # Copy the registered binary into mc_images.tif batch by batch, instead of
    # reading the whole movie into memory and then copying it once more
    mc_images = ImageData(
        binary_to_tiff(io, ops, output_dir, "mc_images"),
        output_dir=output_dir,
        file_name="mc_images",
    )

    info = {
        "refImg": ImageData(ops["refImg"], output_dir=output_dir, file_name="refImg"),
        "meanImgE": ImageData(
            ops["meanImgE"], output_dir=output_dir, file_name="meanImgE"
        ),
        "mc_images": mc_images,
        "ops": Suite2pData(ops, file_name="ops"),
    }

    return info


# tifffile.imwrite switches to BigTIFF above this many bytes (its `bigsize`)
BIGTIFF_BYTES = 2**32 - 2**25
# frames per read, bounded in bytes: suite2p's BinaryFile.read holds the raw
# buffer and its astype copy at once
BATCH_BYTES = 64 << 20


def binary_to_tiff(io, ops, output_dir, file_name, batch_size=None):
    """
    Write the registered binary to the TIFF path ImageData would use.

    Byte-identical to writing the whole array (develop-main's ImageData(mv)),
    with one batch in memory. tifffile picks BigTIFF from `data.nbytes`, which a
    frame iterator does not have, so the choice is made here the same way.
    The file is written beside the final path and moved there when complete,
    so a failed write leaves no partial mc_images.tif.
    `io` is suite2p.io, passed in because suite2p is imported lazily here.
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

    Ly, Lx = ops["Ly"], ops["Lx"]
    frame_bytes = Ly * Lx * np.dtype(np.int16).itemsize
    if batch_size is None:
        batch_size = max(1, BATCH_BYTES // frame_bytes)

    try:
        with io.BinaryFile(Ly=Ly, Lx=Lx, read_filename=ops["reg_file"]) as binary:
            nframes = binary.n_frames

            def frames():
                for _, batch in binary.iter_frames(
                    batch_size=batch_size, dtype=np.int16
                ):
                    yield from batch

            # tifffile stores these shapes as RGB, which 2022.3 cannot stream
            if nframes in (3, 4) or Lx in (3, 4):
                tifffile.imwrite(tmp_path, np.stack(list(frames())))
            else:
                tifffile.imwrite(
                    tmp_path,
                    frames(),
                    shape=(nframes, Ly, Lx),
                    dtype=np.int16,
                    bigtiff=nframes * frame_bytes > BIGTIFF_BYTES,
                )
        os.replace(tmp_path, tiff_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    return [tiff_path]
