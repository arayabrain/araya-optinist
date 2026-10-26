import numpy as np

from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.dataclass import ImageData
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import EditRoiData, FluoData, IscellData, RoiData
from studio.app.optinist.dataclass.roi import RoiMasks, edit_images
from studio.app.optinist.wrappers.optinist.utils import recursive_flatten_params

logger = AppLogger.get_logger()

# Bytes of float64 frames LoadData reads at a time outside LCCD's own windows
BATCH_BYTES = 64 << 20


def lccd_detect(
    mc_images: ImageData, output_dir: str, params: dict = None, **kwargs
) -> dict(fluorescence=FluoData, iscell=IscellData):
    from studio.app.optinist.wrappers.lccd.lccd_python.lccd import LCCD

    function_id = ExptOutputPathIds(output_dir).function_id
    logger.info("start lccd_detect: %s", function_id)

    flattened_params = {}
    recursive_flatten_params(params, flattened_params)
    params = flattened_params

    logger.debug("params: %s", params)
    lccd = LCCD(params)
    with LoadData(mc_images) as D:
        assert (
            len(D.shape) == 3
        ), "input array should have dimensions (width, height, time)"
        try:
            roi = lccd.apply(D)
            num_cell = roi.shape[1]
        except ValueError as e:
            if "No roi region found" in str(e):
                logger.warning("No ROIs found in LCCD detection")
                num_cell = 0
                roi = np.zeros((D.shape[0] * D.shape[1], 0))  # Empty ROIs
            else:
                raise e
        timeseries = D.roi_means(roi)

    num_frames = D.shape[2]

    # Handle zero ROIs case
    if num_cell == 0:
        empty_roi = np.full(D.shape[:2], np.nan)
        im = np.zeros((0, *D.shape[:2]))  # Empty stack of ROIs
        iscell = np.array([], dtype=int)
        timeseries_dff = np.zeros((0, num_frames))
    else:
        dff_f0_frames = params["f0_frames"]
        dff_f0_percentile = params["f0_percentile"]
        iscell = np.ones(num_cell, dtype=int)

        roi_list = []
        for i in range(num_cell):
            roi_list.append((roi[:, i].reshape([D.shape[0], D.shape[1]])) * (i + 1))

        im = np.stack(roi_list)
        im = im.astype(np.float64)
        im[im == 0] = np.nan
        im -= 1

        empty_roi = np.full_like(im[0], np.nan)

        timeseries_dff = np.ones([num_cell, num_frames]) * np.nan
        for i in range(num_cell):
            for k in range(num_frames):
                if (k - dff_f0_frames >= 0) and (k + dff_f0_frames < num_frames):
                    f0 = np.percentile(
                        timeseries[i, k - dff_f0_frames : k + dff_f0_frames],
                        dff_f0_percentile,
                    )
                    timeseries_dff[i, k] = (timeseries[i, k] - f0) / f0

    # Create ROI list for NWB
    roi_list = [{"image_mask": roi[:, i].reshape(D.shape[:2])} for i in range(num_cell)]

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
                "region": list(range(len(timeseries))),
                "name": "Fluorescence",
                "data": timeseries,
                "unit": "lumens",
                "rate": nwbfile.get("imaging_plane", {}).get("imaging_rate", 30),
            }
        }
    }

    info = {
        "dff": FluoData(timeseries_dff, file_name="dff"),
        "fluorescence": FluoData(timeseries, file_name="fluorescence"),
        "iscell": IscellData(iscell),
        "all_roi": RoiData(
            np.nanmax(im, axis=0) if len(im) > 0 else empty_roi,
            output_dir=output_dir,
            file_name="all_roi",
        ),
        "cell_roi": RoiData(
            np.nanmax(im[iscell != 0], axis=0) if len(im) > 0 else empty_roi,
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
            roi_masks=RoiMasks.from_pixels(
                D.shape[:2], (np.flatnonzero(roi[:, i]) for i in range(num_cell))
            ),
        ),
        "nwbfile": nwbfile,
    }

    return info


class LoadData:
    """
    The movie LCCD detects on: (X, Y, frames), float64, scaled to [0, 1] by its
    min and max. Frames are read from the first TIFF of mc_images when sliced:
    - LCCD.apply slices `[:, :, start:stop]`, one window at a time
    - roi_means reads BATCH_BYTES of frames at a time
    so the whole movie is never held.
    """

    def __init__(self, mc_images: ImageData):
        from PIL import Image

        self._pile = Image.open(mc_images.path[0])
        try:
            self.shape = (*self._pile.size, self._pile.n_frames)
            lows, highs = [], []
            for start, stop in self._batches():
                frames = self._read(range(start, stop))
                lows.append(np.min(frames))
                highs.append(np.max(frames))
            self.minval, self.maxval = np.min(lows), np.max(highs)
        except BaseException:
            self._pile.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._pile.close()

    def __getitem__(self, key) -> np.ndarray:
        rows, cols, frames = key
        window = self._read(range(*frames.indices(self.shape[2])))
        window -= self.minval
        window /= self.maxval - self.minval
        return window[rows, cols]

    def roi_means(self, roi: np.ndarray) -> np.ndarray:
        """Each ROI's (column of roi's) mean over its pixels, per frame."""
        X, Y, T = self.shape
        timeseries = np.zeros([roi.shape[1], T])
        if roi.shape[1] == 0:
            return timeseries
        pixels = [np.flatnonzero(roi[:, i] > 0) for i in range(roi.shape[1])]
        for start, stop in self._batches():
            frames = self[:, :, start:stop].reshape(X * Y, stop - start)
            for i, p in enumerate(pixels):
                timeseries[i, start:stop] = np.mean(frames[p, :], axis=0)
        return timeseries

    def _read(self, frames: range) -> np.ndarray:
        window = np.empty((*self.shape[:2], len(frames)))
        for j, i in enumerate(frames):
            self._pile.seek(i)
            window[:, :, j] = np.asarray(self._pile)
        return window

    def _batches(self):
        """
        (start, stop) ranges of about BATCH_BYTES of float64 frames, each at
        least 2 frames wide (unless the movie is 1 frame): a mean over a
        1-frame-wide array sums pixels pairwise, not in order, so it can differ
        in the last bit from the same frame in a wider array.
        """
        X, Y, T = self.shape
        size = max(2, BATCH_BYTES // (X * Y * np.dtype(np.float64).itemsize))
        edges = list(range(0, T, size)) + [T]
        if len(edges) > 2 and edges[-1] - edges[-2] == 1:
            del edges[-2]  # fold a 1-frame tail into the batch before it
        return zip(edges[:-1], edges[1:])
