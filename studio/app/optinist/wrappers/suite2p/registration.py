from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.dataclass import ImageData
from studio.app.optinist.dataclass import Suite2pData
from studio.app.optinist.wrappers.suite2p.utils import (
    binary_to_tiff,
    load_settings,
    pair,
)

logger = AppLogger.get_logger()


def suite2p_registration(
    ops: Suite2pData, output_dir: str, params: dict = None, **kwargs
) -> dict(ops=Suite2pData, mc_images=ImageData):
    from suite2p import default_settings, io, pipeline
    from suite2p.run_s2p import _assign_torch_device

    function_id = ExptOutputPathIds(output_dir).function_id
    logger.info("start suite2p registration: %s", function_id)

    ops = ops.data
    settings = load_settings(default_settings(), "suite2p_registration", params)
    settings["run"]["do_detection"] = False
    reg = settings["registration"]
    # suite2p slices with it
    reg["bidiphase"] = int(reg["bidiphase"])
    reg["block_size"] = pair(reg["block_size"], "registration.block_size")
    if reg["nonrigid"] and any(
        size >= frame for size, frame in zip(reg["block_size"], (ops["Ly"], ops["Lx"]))
    ):
        # one block along an axis gives suite2p an empty shift field to interpolate
        logger.warning(
            "block_size %s is not below the %sx%s frame: rigid registration only",
            reg["block_size"],
            ops["Ly"],
            ops["Lx"],
        )
        reg["nonrigid"] = False
    if reg["smooth_sigma_time"] > 0:
        # the phase correlations are smoothed over each batch's frames, so the
        # batch edges shape the shifts: keep suite2p_file_convert's batches
        reg["batch_size"] = ops["batch_size"]
    device = _assign_torch_device(settings["torch_device"])

    logger.info("registering in batches of %s frames", reg["batch_size"])

    # Registers the binary in place and writes reg_outputs.npy into output_dir;
    # mc_images.tif is copied from the registered binary batch by batch
    with io.BinaryFile(
        Ly=ops["Ly"],
        Lx=ops["Lx"],
        filename=ops["reg_file"],
        n_frames=ops["nframes"],
        write=True,
    ) as f_reg:
        reg_outputs = pipeline(
            output_dir, f_reg, run_registration=True, settings=settings, device=device
        )[0]
        mc_images = ImageData(
            binary_to_tiff(f_reg, output_dir, "mc_images"),
            output_dir=output_dir,
            file_name="mc_images",
        )

    ops = {**ops, **reg_outputs, "settings": settings}

    info = {
        "refImg": ImageData(ops["refImg"], output_dir=output_dir, file_name="refImg"),
        "meanImgE": ImageData(
            ops["meanImgE"], output_dir=output_dir, file_name="meanImgE"
        ),
        "mc_images": mc_images,
        "ops": Suite2pData(ops, file_name="ops"),
    }

    return info
