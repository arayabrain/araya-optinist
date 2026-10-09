import os

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

    ops = {**default_ops(), **ops, **params}

    # register binary
    ops = registration.register_binary(ops, refImg=refImg)

    # Benchmark-only toggles (#893 / #531 sizing): OPTINIST_BENCH_* env vars.
    # Unset in normal runs, so behaviour is unchanged.
    skip_pc_metrics = os.environ.get("OPTINIST_BENCH_SKIP_PC_METRICS") == "1"
    stream_mc_images = os.environ.get("OPTINIST_BENCH_STREAM_MC_IMAGES") == "1"

    # compute metrics for registration
    if (
        not skip_pc_metrics
        and ops.get("do_regmetrics", True)
        and ops["nframes"] >= 1500
    ):
        ops = registration.get_pc_metrics(ops)

    if stream_mc_images:
        mc_images = ImageData(
            _write_binary_as_tiff(io, ops, output_dir, "mc_images"),
            output_dir=output_dir,
            file_name="mc_images",
        )
    else:
        mv = io.BinaryFile(
            Lx=ops["Lx"], Ly=ops["Ly"], read_filename=ops["reg_file"]
        ).data.copy()
        mc_images = ImageData(mv, output_dir=output_dir, file_name="mc_images")

    info = {
        "refImg": ImageData(ops["refImg"], output_dir=output_dir, file_name="refImg"),
        "meanImgE": ImageData(
            ops["meanImgE"], output_dir=output_dir, file_name="meanImgE"
        ),
        "mc_images": mc_images,
        "ops": Suite2pData(ops, file_name="ops"),
    }

    return info


def _write_binary_as_tiff(io, ops, output_dir, file_name, batch_size=500):
    """Write the registered binary to the path ImageData would use, in batches."""
    import numpy as np
    import tifffile

    from studio.app.common.core.utils.filepath_creater import (
        create_directory,
        join_filepath,
    )

    tiff_dir = join_filepath([output_dir, "tiff", file_name])
    create_directory(tiff_dir)
    tiff_path = join_filepath([tiff_dir, f"{file_name}.tif"])

    with io.BinaryFile(
        Lx=ops["Lx"], Ly=ops["Ly"], read_filename=ops["reg_file"]
    ) as binary, tifffile.TiffWriter(tiff_path, bigtiff=True) as writer:
        for _, frames in binary.iter_frames(batch_size=batch_size, dtype=np.int16):
            for frame in frames:
                writer.write(frame, contiguous=True)

    return [tiff_path]
