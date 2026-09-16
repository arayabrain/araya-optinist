import numpy as np

from studio.app.optinist.core.edit_ROI.wrappers.suite2p_edit_roi.utils import (
    complete_stat,
    extract_traces,
    get_stat0_add_roi,
    merge_stat,
    set_nwbfile,
)
from studio.app.optinist.dataclass import (
    EditRoiData,
    FluoData,
    IscellData,
    RoiData,
    Suite2pData,
)
from studio.app.optinist.schemas.roi import RoiPos


def commit_edit(data: EditRoiData, ops: Suite2pData, iscell, node_dirpath, function_id):
    from studio.app.optinist.core.edit_ROI.edit_ROI import CellType

    ops = ops.data
    stat = ops["stat"]
    temp_roi_data_dict = {**data.temp_add_roi, **data.temp_merge_roi}
    temp_roi_data_dict = dict(sorted(temp_roi_data_dict.items()))

    for roi_id, roi_data in temp_roi_data_dict.items():
        # A pending ROI the user deleted before committing still has its temp
        # entry, and its trace is still appended to keep F aligned with im.
        if iscell[int(roi_id)] != CellType.TEMP_DELETE:
            iscell[int(roi_id)] = CellType.ROI
        if isinstance(roi_data, RoiPos):
            # added roi: extract its traces from the registered movie
            stat0 = get_stat0_add_roi(
                ops, roi_data.posx, roi_data.posy, roi_data.sizex, roi_data.sizey
            )
            stat0 = complete_stat(stat0, stat, ops)
            F, Fneu = extract_traces(ops, stat + [stat0], [stat0])
        else:
            # merged roi: average the traces of its sources
            stat0 = complete_stat(merge_stat(stat, roi_data), stat, ops)
            F = np.mean(ops["F"][roi_data, :], axis=0, keepdims=True)
            Fneu = np.mean(ops["Fneu"][roi_data, :], axis=0, keepdims=True)

        ops["F"] = np.concatenate((ops["F"], F), axis=0)
        ops["Fneu"] = np.concatenate((ops["Fneu"], Fneu), axis=0)
        stat.append(stat0)

    iscell[iscell == CellType.TEMP_DELETE] = CellType.NON_ROI

    ops["stat"] = stat
    data.commit()

    info = {
        "ops": Suite2pData(ops),
        "fluorescence": FluoData(ops["F"], file_name="fluorescence"),
        "iscell": IscellData(iscell),
        "cell_roi": RoiData(
            np.nanmax(data.im[iscell != CellType.NON_ROI], axis=0, initial=np.nan),
            output_dir=node_dirpath,
            file_name="cell_roi",
        ),
        "edit_roi_data": data,
        "nwbfile": set_nwbfile(ops, iscell, data, function_id),
    }
    return info
