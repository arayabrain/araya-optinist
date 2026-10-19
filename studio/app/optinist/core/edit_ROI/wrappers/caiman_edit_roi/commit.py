import numpy as np

from studio.app.common.dataclass.image import ImageData
from studio.app.optinist.core.edit_ROI.wrappers.caiman_edit_roi.utils import set_nwbfile
from studio.app.optinist.dataclass import EditRoiData, FluoData, IscellData, RoiData
from studio.app.optinist.dataclass.roi import open_edit_images


def commit_edit(
    images: ImageData,
    data: EditRoiData,
    fluorescence: FluoData,
    iscell,
    node_dirpath,
    function_id,
):
    from studio.app.optinist.core.edit_ROI.edit_ROI import CellType

    fluorescence = fluorescence.data
    num_cell = data.im.shape[0]
    # Only new traces read the movie, so a commit that only deletes ROIs never
    # opens it. A node without traces also takes its frame count from it
    # (FluoData stores no traces as (0, 0)).
    if num_cell > len(fluorescence) or fluorescence.size == 0:
        images = open_edit_images(images, node_dirpath)

    if fluorescence.size > 0:
        new_fluorescences = np.zeros((num_cell, fluorescence.shape[1]))
        new_fluorescences[: len(fluorescence)] = fluorescence
    else:
        # If starting with no ROIs, initialize with correct time dimension
        new_fluorescences = np.zeros((num_cell, images.shape[0]))

    num_committed = len(fluorescence)
    added = iscell == CellType.TEMP_ADD
    iscell[iscell == CellType.TEMP_DELETE] = CellType.NON_ROI
    for i in range(num_committed, num_cell):
        # The movie CaImAn works on is float32: average in float32 so a
        # reference to a uint16 input gives the same means as CaImAn's copy did
        new_fluorescences[i] = np.mean(
            images[:, ~np.isnan(data.im[i])], axis=1, dtype=np.float32
        )
        if added[i]:
            iscell[i] = CellType.ROI

    data.commit()

    info = {
        "cell_roi": RoiData(
            np.nanmax(data.im[iscell != CellType.NON_ROI], axis=0, initial=np.nan),
            output_dir=node_dirpath,
            file_name="cell_roi",
        ),
        "fluorescence": FluoData(new_fluorescences, file_name="fluorescence"),
        "iscell": IscellData(iscell),
        "edit_roi_data": data,
        "nwbfile": set_nwbfile(data, iscell, function_id, new_fluorescences),
    }
    return info
