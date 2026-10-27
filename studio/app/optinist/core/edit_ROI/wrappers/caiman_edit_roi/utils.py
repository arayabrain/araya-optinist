import numpy as np

from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass.roi import EditRoiData, nwb_roi_label_image


def set_nwbfile(
    edit_roi_data: EditRoiData, iscell, function_id, rate, fluorescence=None
):
    # NWBの追加
    nwbfile = {}

    # NWBにROIを追加
    roi_list = []
    roi_masks = edit_roi_data.roi_masks
    n_cells = len(roi_masks)
    if n_cells == 0:
        # Add a dummy ROI entry if empty to maintain table structure
        roi_list.append({"pixel_mask": np.zeros((1, 3))})  # (row, column, weight)
        iscell = np.array([[0, 0]])  # [iscell, probcell]
    else:
        for i in range(n_cells):
            kargs = {}
            # (row, column, weight): weight 1 on the ROI's footprint
            rows, cols = roi_masks.coords(i)
            kargs["pixel_mask"] = np.array([rows, cols, np.ones(rows.size)]).T
            roi_list.append(kargs)

    nwbfile[NWBDATASET.ROI] = {function_id: {"roi_list": roi_list}}

    nwbfile[NWBDATASET.COLUMN] = {
        function_id: {
            "name": "iscell",
            "description": "two columns - iscell & probcell",
            "data": iscell,
        }
    }

    # Fluorescence

    nwbfile[NWBDATASET.FLUORESCENCE] = {
        function_id: {
            "Fluorescence": {
                "table_name": "ROIs",
                "region": list(range(n_cells)) if n_cells > 0 else [0],
                "name": "Fluorescence",
                "data": fluorescence
                if fluorescence is not None and fluorescence.size > 0
                else np.array([]).reshape(0, 0),
                "unit": "lumens",
                "rate": rate,
            },
        }
    }

    # NWB追加
    nwbfile[NWBDATASET.POSTPROCESS] = {
        function_id: {
            **nwb_roi_label_image(roi_masks.label_image()),
            "add_roi": edit_roi_data.add_roi,
            "delete_roi": edit_roi_data.delete_roi,
            "merge_roi": edit_roi_data.merge_roi,
        }
    }

    return nwbfile
