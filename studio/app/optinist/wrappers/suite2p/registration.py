from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.workflow.workflow_params import read_default_params
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

    # A node saved without these keys (its parameter panel cannot add them)
    # takes this node's shipped defaults
    shipped_yaml = read_default_params("suite2p_registration")
    if not shipped_yaml:
        raise FileNotFoundError("suite2p_registration.yaml not found")
    shipped_params = {}
    recursive_flatten_params(shipped_yaml, shipped_params)
    for key in ("batch_size", "do_regmetrics"):
        params.setdefault(key, shipped_params[key])

    ops = ops.data
    refImg = ops["meanImg"]

    # REGISTRATION
    if len(refImg.shape) == 3:
        refImg = refImg[0]

    # This node's batch_size sizes registration only (its peak memory grows
    # with it); downstream nodes keep the batch_size they were given.
    input_batch_size = ops.get("batch_size", default_ops()["batch_size"])
    ops = {**default_ops(), **ops, **params}

    # smooth_sigma_time > 0 smooths over the frames of each batch, so the batch
    # edges shape the shifts: register with the incoming batch_size then
    smoothing = ops.get("smooth_sigma_time", 0) > 0
    if smoothing:
        ops["batch_size"] = input_batch_size
    logger.info(
        "registering in batches of %s frames%s",
        ops["batch_size"],
        " (smooth_sigma_time > 0: the incoming batch_size)" if smoothing else "",
    )

    # register binary
    ops = registration.register_binary(ops, refImg=refImg)

    # compute metrics for registration (QC only; off by default)
    if ops.get("do_regmetrics", True) and ops["nframes"] >= 1500:
        ops = registration.get_pc_metrics(ops)

    # Record the batch registration used; pass the incoming one on
    ops["reg_batch_size"] = ops["batch_size"]
    ops["batch_size"] = input_batch_size

    mv = io.BinaryFile(
        Lx=ops["Lx"], Ly=ops["Ly"], read_filename=ops["reg_file"]
    ).data.copy()

    info = {
        "refImg": ImageData(ops["refImg"], output_dir=output_dir, file_name="refImg"),
        "meanImgE": ImageData(
            ops["meanImgE"], output_dir=output_dir, file_name="meanImgE"
        ),
        "mc_images": ImageData(mv, output_dir=output_dir, file_name="mc_images"),
        "ops": Suite2pData(ops, file_name="ops"),
    }

    return info
