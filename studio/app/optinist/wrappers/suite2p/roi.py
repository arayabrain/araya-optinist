from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.common.dataclass import ImageData
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import (
    EditRoiData,
    FluoData,
    IscellData,
    RoiData,
    Suite2pData,
)
from studio.app.optinist.dataclass.roi import RoiStack
from studio.app.optinist.wrappers.optinist.utils import recursive_flatten_params

logger = AppLogger.get_logger()


def suite2p_roi(
    ops: Suite2pData, output_dir: str, params: dict = None, **kwargs
) -> dict(ops=Suite2pData, fluorescence=FluoData, iscell=IscellData):
    import numpy as np
    from suite2p import classification, default_ops, detection, extraction

    function_id = ExptOutputPathIds(output_dir).function_id
    logger.info("start suite2p_roi: %s", function_id)

    flattened_params = {}
    recursive_flatten_params(params, flattened_params)
    params = flattened_params

    nwbfile = kwargs.get("nwbfile", {})
    fs = nwbfile.get("imaging_plane", {}).get("imaging_rate", 30)

    ops = ops.data
    ops = {**default_ops(), **ops, **params, "fs": fs}

    # Initialize default empty outputs
    empty_roi = np.full((ops["Ly"], ops["Lx"]), np.nan)
    im = np.zeros((0, ops["Ly"], ops["Lx"]))
    F = np.zeros((0, ops["nframes"]))
    Fneu = np.zeros((0, ops["nframes"]))
    iscell = np.array([], dtype=int)
    stat = []
    roi_images = None

    try:
        # ROI detection
        ops_classfile = ops.get("classifier_path")
        builtin_classfile = classification.builtin_classfile
        user_classfile = classification.user_classfile

        if ops_classfile:
            logger.info(f"NOTE: applying classifier {str(ops_classfile)}")
            classfile = ops_classfile
        elif ops["use_builtin_classifier"] or not user_classfile.is_file():
            logger.info(
                f"NOTE: Applying builtin classifier at {str(builtin_classfile)}"
            )
            classfile = builtin_classfile
        else:
            logger.info(f"NOTE: applying default {str(user_classfile)}")
            classfile = user_classfile

        # Check if input data exists
        if not ops.get("filelist") or len(ops["filelist"]) == 0:
            logger.warning("No input files found. Returning empty results.")
        else:
            ops, stat = detection.detect(ops=ops, classfile=classfile)

            if len(stat) > 0:
                # ROI EXTRACTION
                ops, stat, F, Fneu, _, _ = extraction.create_masks_and_extract(
                    ops, stat
                )
                stat = stat.tolist()

                # ROI CLASSIFICATION
                iscell = classification.classify(stat=stat, classfile=classfile)
                iscell = iscell[:, 0].astype(int)
            else:
                logger.info("No ROIs detected in the data.")

    except Exception as e:
        logger.warning(f"Error during ROI detection: {str(e)}")
        # Continue with empty results

    # Outside the handler: a failed write must fail the node, not empty its images
    if len(iscell) > 0:
        # The per-ROI mask stack (NaN outside ROI i, i inside it), written
        # to disk one ROI at a time and referenced rather than held
        stack, stack_path = RoiStack.create(
            join_filepath([output_dir, "roi_mask_stack.npy"]),
            (len(stat), ops["Ly"], ops["Lx"]),
        )
        for i, s in enumerate(stat):
            stack[i, s["ypix"], s["xpix"]] = i
        stack.flush()
        del stack
        im = RoiStack.open(stack_path)
        roi_images = roi_label_images(stat, iscell, ops["Ly"], ops["Lx"])

    # Create ROI list
    roi_list = []
    for i in range(len(stat)):
        kargs = {}
        kargs["pixel_mask"] = np.array(
            [stat[i]["ypix"], stat[i]["xpix"], stat[i]["lam"]]
        ).T
        roi_list.append(kargs)

    # Prepare NWB output
    nwbfile = {}
    nwbfile[NWBDATASET.ROI] = {function_id: {"roi_list": roi_list}}
    nwbfile[NWBDATASET.POSTPROCESS] = {function_id: {"all_roi_img": im}}
    nwbfile[NWBDATASET.COLUMN] = {
        function_id: {
            "name": "iscell",
            "description": "two columns - iscell & probcell",
            "data": iscell,
        }
    }
    nwbfile[NWBDATASET.FLUORESCENCE] = {
        function_id: {
            "Fluorescence": {
                "table_name": "Fluorescence",
                "region": list(range(len(F))),
                "name": "Fluorescence",
                "data": F,
                "unit": "lumens",
                "rate": ops["fs"],
            },
            "Neuropil": {
                "table_name": "Neuropil",
                "region": list(range(len(Fneu))),
                "name": "Neuropil",
                "data": Fneu,
                "unit": "lumens",
                "rate": ops["fs"],
            },
        }
    }

    # Update ops with extracted data
    ops["stat"] = stat
    ops["F"] = F
    ops["Fneu"] = Fneu

    # Prepare output info
    info = {
        "ops": Suite2pData(ops),
        "max_proj": ImageData(
            ops["max_proj"], output_dir=output_dir, file_name="max_proj"
        ),
        "Vcorr": ImageData(ops["Vcorr"], output_dir=output_dir, file_name="Vcorr"),
        "fluorescence": FluoData(F, file_name="fluorescence"),
        "iscell": IscellData(iscell, file_name="iscell"),
        "all_roi": RoiData(
            roi_images["all"] if roi_images else empty_roi,
            output_dir=output_dir,
            file_name="all_roi",
        ),
        "non_cell_roi": RoiData(
            roi_images["non_cell"] if roi_images else empty_roi,
            output_dir=output_dir,
            file_name="noncell_roi",
        ),
        "cell_roi": RoiData(
            roi_images["cell"] if roi_images else empty_roi,
            output_dir=output_dir,
            file_name="cell_roi",
        ),
        # A reference to the movie, not the movie: suite2p's ROI editing reads
        # traces from the registered binary (ops["reg_file"]), never from this
        "edit_roi_data": EditRoiData(images=ImageData(ops["filelist"]), im=im),
        "nwbfile": nwbfile,
    }

    return info


def roi_label_images(stat, iscell, Ly, Lx):
    """
    The all / cell / non-cell ROI images, from the ROIs' pixel lists.

    Each pixel holds the highest ROI index covering it, which is what nanmax
    over the per-ROI stack gives (layer i is i on ROI i's pixels, NaN elsewhere)
    without reading the stack.
    """
    import numpy as np

    images = {k: np.full((Ly, Lx), np.nan) for k in ("all", "cell", "non_cell")}
    for i, s in enumerate(stat):
        group = "cell" if iscell[i] != 0 else "non_cell"
        for key in ("all", group):
            images[key][s["ypix"], s["xpix"]] = i
    return images
