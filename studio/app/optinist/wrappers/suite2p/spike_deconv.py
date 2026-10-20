from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import SpikingActivityData, Suite2pData
from studio.app.optinist.wrappers.suite2p.utils import load_settings, positive

logger = AppLogger.get_logger()

BASELINES = ("maximin", "constant", "prctile")


def suite2p_spike_deconv(
    ops: Suite2pData, output_dir: str, params: dict = None, **kwargs
) -> dict(ops=Suite2pData, spks=SpikingActivityData):
    import numpy as np
    import torch
    from suite2p import default_settings, extraction

    function_id = ExptOutputPathIds(output_dir).function_id
    logger.info("start suite2_spike_deconv: %s", function_id)

    settings = load_settings(default_settings(), "suite2p_spike_deconv", params)
    preprocess = settings["dcnv_preprocess"]
    baseline = preprocess["baseline"]
    if baseline not in BASELINES:
        # suite2p subtracts no baseline at all for any other value
        raise ValueError(f"baseline must be one of {BASELINES}, got '{baseline}'")
    # a non-positive batch runs no batch at all and leaves the spikes at zero;
    # a zero smoothing or window gives NaN baselines, with no error either way
    batch_size = positive(settings["extraction"]["batch_size"], "extraction.batch_size")
    if baseline != "prctile":
        positive(preprocess["sig_baseline"], "dcnv_preprocess.sig_baseline")
    if baseline == "maximin":
        positive(preprocess["win_baseline"], "dcnv_preprocess.win_baseline")

    ops = ops.data
    # pre-upgrade roi pickles store tau flat
    tau = ops.get("settings", {}).get("tau", ops.get("tau", settings["tau"]))

    dF = ops["F"].copy() - settings["extraction"]["neuropil_coefficient"] * ops["Fneu"]
    if dF.shape[0] == 0:
        spks = np.zeros_like(dF)
    else:
        dF = extraction.preprocess(
            F=dF,
            fs=ops["fs"],
            batch_size=batch_size,
            device=torch.device("cpu"),
            **preprocess,
        )
        spks = extraction.oasis(F=dF, batch_size=batch_size, tau=tau, fs=ops["fs"])

    ops["spks"] = spks

    # NWBを追加
    nwbfile = {}

    # roiを追加
    stat = ops["stat"]
    roi_list = []
    for i in range(len(stat)):
        kargs = {}
        kargs["pixel_mask"] = np.array(
            [stat[i]["ypix"], stat[i]["xpix"], stat[i]["lam"]]
        ).T
        roi_list.append(kargs)

    nwbfile[NWBDATASET.ROI] = {function_id: {"roi_list": roi_list}}

    # Fluorenceを追加
    nwbfile[NWBDATASET.FLUORESCENCE] = {
        function_id: {
            "Deconvolved": {
                "table_name": "Deconvolved",
                "region": list(range(len(spks))),
                "name": function_id + "_Deconvolved",
                "data": spks,
                "unit": "lumens",
                "rate": ops["fs"],
            }
        }
    }

    info = {
        "ops": Suite2pData(ops),
        "spks": SpikingActivityData(spks, file_name="spks"),
        "nwbfile": nwbfile,
    }

    return info
