import os
import shutil

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
from studio.app.optinist.dataclass.roi import RoiMasks, RoiStack
from studio.app.optinist.wrappers.suite2p.utils import load_settings, pair, positive

logger = AppLogger.get_logger()

ALGORITHMS = ("sparsery", "sourcery")
# what pipeline() reads back from the registration step
REG_KEYS = ("yrange", "xrange", "badframes")


def suite2p_roi(
    ops: Suite2pData, output_dir: str, params: dict = None, **kwargs
) -> dict(ops=Suite2pData, fluorescence=FluoData, iscell=IscellData):
    import numpy as np
    from suite2p import default_settings, io, pipeline
    from suite2p.run_s2p import _assign_torch_device

    function_id = ExptOutputPathIds(output_dir).function_id
    logger.info("start suite2p_roi: %s", function_id)

    nwbfile = kwargs.get("nwbfile", {})
    fs = nwbfile.get("imaging_plane", {}).get("imaging_rate", 30)

    if params and "detection" not in params:
        # a pre-1.1.0 tree arrives here with only tau and classification left
        raise KeyError(
            "suite2p_roi parameters predate suite2p 1.1.0: "
            "delete the node and add it again"
        )
    settings = load_settings(default_settings(), "suite2p_roi", params)
    settings["fs"] = fs
    settings["run"]["do_deconvolution"] = False
    settings["torch_device"] = "cpu"
    settings["diameter"] = pair(settings["diameter"], "diameter", float)
    algorithm = settings["detection"]["algorithm"]
    if algorithm not in ALGORITHMS:
        raise ValueError(
            f"detection.algorithm must be one of {ALGORITHMS}: {algorithm}"
        )
    if not settings["detection"]["max_overlap"] < 1:
        # suite2p omits stat["overlap"] otherwise, which ROI editing reads
        raise ValueError("detection.max_overlap must be below 1")
    tau = positive(settings["tau"], "tau")
    positive(fs, "NWB imaging_rate")
    nbins = positive(settings["detection"]["nbins"], "detection.nbins")
    # zero or below accepts every residual peak as an ROI, or runs no
    # extraction batch and classifies flat traces, with no error either way
    positive(settings["detection"]["threshold_scaling"], "detection.threshold_scaling")
    positive(settings["extraction"]["batch_size"], "extraction.batch_size")
    device = _assign_torch_device(settings["torch_device"])

    ops = ops.data
    Ly, Lx, nframes = ops["Ly"], ops["Lx"], ops["nframes"]
    # suite2p's detection bin, averaged within 500-frame batches of the good
    # frames: a bin wider than a batch is a bare ZeroDivisionError in bin_movie
    bin_size = int(max(1, nframes // nbins, np.round(tau * fs)))
    n_good = nframes - int(np.sum(ops["badframes"])) if "badframes" in ops else nframes
    if bin_size > min(500, n_good):
        raise ValueError(
            f"detection bins {bin_size} frames (tau x imaging_rate, or nframes / "
            f"nbins) but only {n_good} usable frames of {nframes} fit a 500-frame "
            "batch: lower tau or raise nbins"
        )

    # pipeline() reads the registration step's frame range here and writes
    # copies of its outputs (F, Fneu, stat, ...) beside it; the pickle keeps them
    work_dir = join_filepath([output_dir, "suite2p"])
    shutil.rmtree(work_dir, ignore_errors=True)
    os.makedirs(work_dir)
    # Without a registration node suite2p falls back to the full frame
    if all(key in ops for key in REG_KEYS):
        reg_path = join_filepath([work_dir, "reg_outputs.npy"])
        np.save(reg_path, {key: ops[key] for key in REG_KEYS})
    ops.pop("aspect", None)  # 0.10.3 only; ROI editing would prefer it to diameter
    ops["fs"] = fs
    ops["settings"] = settings

    # Initialize default empty outputs
    im = np.zeros((0, Ly, Lx))
    F = np.zeros((0, nframes))
    Fneu = np.zeros((0, nframes))
    iscell = np.array([], dtype=int)
    stat = []
    ops["max_proj"] = np.zeros((Ly, Lx))
    ops["Vcorr"] = np.zeros((Ly, Lx))
    ops["diameter"] = settings["diameter"]

    try:
        with io.BinaryFile(
            Ly=Ly, Lx=Lx, filename=ops["reg_file"], n_frames=nframes
        ) as f_reg:
            out = pipeline(
                work_dir,
                f_reg,
                run_registration=False,
                settings=settings,
                device=device,
            )
    except ValueError as e:
        # suite2p raises when detection, or preclassify, leaves no ROI; any
        # other error fails the node
        if "no ROIs" not in str(e):
            raise
        logger.warning("No ROIs detected: %s", e)
        out = (None,) * 12
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    detect_outputs, stat_, F_, Fneu_, iscell_ = out[1], out[2], out[3], out[4], out[8]
    if detect_outputs is not None:
        ops.update(detect_outputs)  # max_proj, Vcorr, meanImg_crop, diameter
    if F_ is None:
        logger.info("No ROIs detected in the data.")
    else:
        stat, F, Fneu = list(stat_), F_, Fneu_
        iscell = iscell_[:, 0].astype(int)

    # Outside the handler: a failed write must fail the node, not empty its images
    if len(iscell) > 0:
        # The per-ROI mask stack (NaN outside ROI i, i inside it), written
        # to disk one ROI at a time and referenced rather than held
        stack_path = os.path.abspath(join_filepath([output_dir, "roi_mask_stack.npy"]))
        stack, tmp_path = RoiStack.create(stack_path + ".tmp", (len(stat), Ly, Lx))
        for i, s in enumerate(stat):
            stack[i, s["ypix"], s["xpix"]] = i
        stack.flush()
        del stack
        # a new inode, so a request still reading the previous stack keeps it
        os.replace(tmp_path, stack_path)
        im = RoiStack.open(stack_path)

    roi_masks = RoiMasks.from_pixels(
        (Ly, Lx),
        (np.ravel_multi_index((s["ypix"], s["xpix"]), (Ly, Lx)) for s in stat),
    )

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
            roi_masks.label_image(),
            output_dir=output_dir,
            file_name="all_roi",
        ),
        "non_cell_roi": RoiData(
            roi_masks.label_image(iscell == 0),
            output_dir=output_dir,
            file_name="noncell_roi",
        ),
        "cell_roi": RoiData(
            roi_masks.label_image(iscell != 0),
            output_dir=output_dir,
            file_name="cell_roi",
        ),
        # A reference to the movie, not the movie: suite2p's ROI editing reads
        # traces from the registered binary (ops["reg_file"]), never from this
        "edit_roi_data": EditRoiData(
            images=ImageData(ops.get("file_list") or ops["filelist"]),
            roi_masks=roi_masks,
        ),
        "nwbfile": nwbfile,
    }

    return info
