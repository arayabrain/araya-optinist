import os

from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.rules.benchmark_recorder import BenchmarkRecorder
from studio.app.common.dataclass import ImageData
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import (
    EditRoiData,
    FluoData,
    IscellData,
    RoiData,
    Suite2pData,
)
from studio.app.optinist.wrappers.optinist.utils import recursive_flatten_params

logger = AppLogger.get_logger()


def suite2p_roi(
    ops: Suite2pData, output_dir: str, params: dict = None, **kwargs
) -> dict(ops=Suite2pData, fluorescence=FluoData, iscell=IscellData):
    import numpy as np
    from suite2p import ROI, classification, default_ops, detection, extraction

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
    roi_label_images = None
    F = np.zeros((0, ops["nframes"]))
    Fneu = np.zeros((0, ops["nframes"]))
    iscell = np.array([], dtype=int)
    stat = []

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
            with BenchmarkRecorder.phase("roi.detect"):
                ops, stat = detection.detect(ops=ops, classfile=classfile)

            if len(stat) > 0:
                # ROI EXTRACTION
                with BenchmarkRecorder.phase("roi.extract"):
                    ops, stat, F, Fneu, _, _ = extraction.create_masks_and_extract(
                        ops, stat
                    )
                stat = stat.tolist()

                # ROI CLASSIFICATION
                with BenchmarkRecorder.phase("roi.classify"):
                    iscell = classification.classify(stat=stat, classfile=classfile)
                    iscell = iscell[:, 0].astype(int)

                # Benchmark-only toggles (#893 / #531 sizing). Unset in normal runs.
                # PREALLOC: same stack, filled in place (no list of full frames).
                # LABEL_ONLY: no stack at all; ROI images from 2D label images.
                #   Breaks ROI editing; never set outside the benchmark.
                with BenchmarkRecorder.phase("roi.stack_masks"):
                    if os.environ.get("OPTINIST_BENCH_ROI_LABEL_ONLY") == "1":
                        roi_label_images = _roi_label_images(
                            np, stat, iscell, ops["Ly"], ops["Lx"]
                        )
                    elif os.environ.get("OPTINIST_BENCH_ROI_PREALLOC") == "1":
                        im = np.full((len(stat), ops["Ly"], ops["Lx"]), np.nan)
                        for i, s in enumerate(stat):
                            im[i, s["ypix"], s["xpix"]] = i
                    else:
                        arrays = []
                        for i, s in enumerate(stat):
                            array = ROI(
                                ypix=s["ypix"],
                                xpix=s["xpix"],
                                lam=s["lam"],
                                med=s["med"],
                                do_crop=False,
                            ).to_array(Ly=ops["Ly"], Lx=ops["Lx"])
                            array *= i + 1
                            arrays.append(array)

                        im = np.stack(arrays)
                        im[im == 0] = np.nan
                        im -= 1
                logger.info("suite2p_roi: %d ROIs, mask stack %s", len(stat), im.shape)
            else:
                logger.info("No ROIs detected in the data.")

    except Exception as e:
        logger.warning(f"Error during ROI detection: {str(e)}")
        # Continue with empty results

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

    with BenchmarkRecorder.phase("roi.build_outputs"):
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
                (
                    roi_label_images["all"]
                    if roi_label_images is not None
                    else np.nanmax(im, axis=0)
                    if len(im) > 0
                    else empty_roi
                ),
                output_dir=output_dir,
                file_name="all_roi",
            ),
            "non_cell_roi": RoiData(
                (
                    roi_label_images["non_cell"]
                    if roi_label_images is not None
                    else (
                        np.nanmax(im[iscell == 0], axis=0) if len(im) > 0 else empty_roi
                    )
                ),
                output_dir=output_dir,
                file_name="noncell_roi",
            ),
            "cell_roi": RoiData(
                (
                    roi_label_images["cell"]
                    if roi_label_images is not None
                    else (
                        np.nanmax(im[iscell != 0], axis=0) if len(im) > 0 else empty_roi
                    )
                ),
                output_dir=output_dir,
                file_name="cell_roi",
            ),
            "edit_roi_data": EditRoiData(
                # Benchmark-only toggle (#893 / #531 sizing): keep the paths, not the
                # movie. Breaks ROI editing; never set outside the benchmark.
                images=(
                    ops["filelist"]
                    if os.environ.get("OPTINIST_BENCH_ROI_IMAGES_PATH") == "1"
                    else ImageData(ops["filelist"]).data
                ),
                im=im,
            ),
            "nwbfile": nwbfile,
        }

    return info


def _roi_label_images(np, stat, iscell, Ly, Lx):
    """2D images equal to nanmax over the per-ROI stack, without building it.

    Each pixel holds the highest ROI index covering it, as nanmax does.
    """
    images = {k: np.full((Ly, Lx), np.nan) for k in ("all", "cell", "non_cell")}
    for i, s in enumerate(stat):
        group = "cell" if iscell[i] != 0 else "non_cell"
        for key in ("all", group):
            images[key][s["ypix"], s["xpix"]] = i
    return images
