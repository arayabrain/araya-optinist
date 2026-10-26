import numpy as np

from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.dataclass import ImageData
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import EditRoiData, FluoData, IscellData, RoiData
from studio.app.optinist.dataclass.roi import RoiMasks, edit_images, nwb_all_roi_img
from studio.app.optinist.wrappers.optinist.utils import recursive_flatten_params

logger = AppLogger.get_logger()


def vacant_roi(
    mc_images: ImageData, output_dir: str, params: dict = None, **kwargs
) -> dict(fluorescence=FluoData, iscell=IscellData):
    function_id = ExptOutputPathIds(output_dir).function_id
    logger.info("start lccd_detect: %s", function_id)

    flattened_params = {}
    recursive_flatten_params(params, flattened_params)
    params = flattened_params

    logger.debug("params: %s", params)

    shape = movie_shape(mc_images)

    num_frames = shape[2]

    # No ROIs: they are all added by ROI editing
    roi_masks = RoiMasks.from_pixels(shape[:2], [])
    iscell = np.array([], dtype=int)
    timeseries = np.zeros((0, num_frames))
    empty_roi = roi_masks.label_image()
    roi_list = []

    nwbfile = kwargs.get("nwbfile", {})
    logger.debug(f"nwbfile: {nwbfile}")
    nwbfile[NWBDATASET.ROI] = {function_id: {"roi_list": roi_list}}
    nwbfile[NWBDATASET.POSTPROCESS] = {
        function_id: {"all_roi_img": nwb_all_roi_img(empty_roi)}
    }

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
                "region": list(range(len(timeseries))),
                "name": "Fluorescence",
                "data": timeseries,
                "unit": "lumens",
                "rate": nwbfile.get("imaging_plane", {}).get("imaging_rate", 30),
            }
        }
    }

    info = {
        "fluorescence": FluoData(timeseries, file_name="fluorescence"),
        "iscell": IscellData(iscell),
        "all_roi": RoiData(
            empty_roi,
            output_dir=output_dir,
            file_name="all_roi",
        ),
        "cell_roi": RoiData(
            empty_roi,
            output_dir=output_dir,
            file_name="cell_roi",
        ),
        "non_cell_roi": RoiData(
            empty_roi, output_dir=output_dir, file_name="non_cell_roi"
        ),
        # A reference to the movie when it lives in this experiment; ROI
        # editing opens it lazily
        "edit_roi_data": EditRoiData(
            images=edit_images(mc_images, output_dir),
            roi_masks=roi_masks,
        ),
        "nwbfile": nwbfile,
    }

    return info


def movie_shape(mc_images):
    """(rows, columns, frames) of the first TIFF of mc_images, from its headers."""
    from PIL import Image

    with Image.open(mc_images.path[0]) as img_pile:
        width, height = img_pile.size
        return (height, width, img_pile.n_frames)
