import numpy as np

from studio.app.common.dataclass.image import ImageData
from studio.app.optinist.dataclass.iscell import IscellData
from studio.app.optinist.dataclass.roi import RoiData


def max_projection(rois):
    # not nanmax(initial=np.nan): it raises on the integer stacks an HDF5 can hold
    return np.nanmax(rois, axis=0) if len(rois) else np.full(rois.shape[1:], np.nan)


def all_roi_image(rois):
    """
    The all-ROI image of an NWB all_roi_img: a label image (each pixel its
    highest ROI; one (1, H, W) layer, or 2D), or the per-ROI stack files
    written before label images hold, max-projected.
    """
    return rois if rois.ndim == 2 else np.nanmax(rois, axis=0)


def group_roi_image(rois, in_group):
    """
    The image of the ROIs where in_group (one entry per ROI) is true. From a
    per-ROI stack (one layer per ROI), the max projection of the group's layers.
    From a label image, the pixels whose ROI is in the group: where a higher
    ROI of the other group covers one, the pixel is NaN. A stack of one ROI is
    its own label image, so either reading gives the same.
    """
    if rois.ndim == 3 and len(rois) == len(in_group):
        return max_projection(rois[in_group])
    label = rois.reshape(rois.shape[-2:])
    return np.where(np.isin(label, np.flatnonzero(in_group)), label, np.nan)


def roi_from_hdf5(
    cell_img: ImageData,
    output_dir: str,
    iscell: IscellData = None,
    params: dict = None,
    **kwargs
) -> dict(iscell=IscellData, all_roi=RoiData, non_cell_roi=RoiData, cell_roi=RoiData):
    rois = cell_img.data
    all_roi = RoiData(all_roi_image(rois), output_dir=output_dir, file_name="roi")
    if iscell is not None:
        iscell_data = iscell.data
        return {
            "iscell": IscellData(iscell_data, file_name="iscell"),
            "all_roi": all_roi,
            "non_cell_roi": RoiData(
                group_roi_image(rois, iscell_data == 0),
                output_dir=output_dir,
                file_name="noncell_roi",
            ),
            "cell_roi": RoiData(
                group_roi_image(rois, iscell_data != 0),
                output_dir=output_dir,
                file_name="cell_roi",
            ),
        }
    else:
        return {"all_roi": all_roi}
