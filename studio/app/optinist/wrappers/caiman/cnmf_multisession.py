import gc

import numpy as np

from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.dataclass import ImageData
from studio.app.common.dataclass.image import open_frames
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import EditRoiData, FluoData, IscellData, RoiData
from studio.app.optinist.dataclass.roi import (
    RoiMasks,
    edit_reference,
    nwb_roi_label_image,
)
from studio.app.optinist.wrappers.caiman.caiman_utils import (
    caiman_cluster,
    distribute_params_to_groups,
    parse_text_params,
)
from studio.app.optinist.wrappers.caiman.cnmf import (
    caiman_dff,
    correlation_image,
    dff_frames_window,
    get_roi,
    nwb_pixel_mask,
    util_cleanup_image_memmap,
    util_download_model_files,
    util_edit_roi_movie,
    util_get_image_memmap_from_files,
)
from studio.app.optinist.wrappers.optinist.utils import recursive_flatten_params

logger = AppLogger.get_logger()

# frames per read for a session template, bounded in bytes
BATCH_BYTES = 64 << 20


class FrameStack:
    """
    The frames of TIFF stacks joined in time, as a (T, d1, d2) array-like that
    reads them when sliced. Each file is memory-mapped where it can be, so a
    slice within one file is a view, and a slice across files a copy of its
    frames.
    """

    def __init__(self, paths: list):
        self.files = [open_frames(path) for path in paths]
        self.starts = np.cumsum([0] + [len(f) for f in self.files])
        self.shape = (int(self.starts[-1]),) + self.files[0].shape[1:]

    def __getitem__(self, key: slice):
        start, stop, _ = key.indices(self.shape[0])
        pieces = [
            f[max(start, s) - s : min(stop, s + len(f)) - s]
            for f, s in zip(self.files, self.starts)
            if max(start, s) < min(stop, s + len(f))
        ]
        return pieces[0] if len(pieces) == 1 else np.concatenate(pieces)


def session_template(path: str):
    """
    caiman.load(path).mean(0) read in batches: the frames as float32, added in
    frame order as numpy reduces a C-order (T, d1, d2) array over its first
    axis, then divided as np.mean divides, so the values are bit-identical.
    """
    frames = FrameStack([path])
    T = frames.shape[0]
    frame_bytes = int(np.prod(frames.shape[1:])) * np.dtype(np.float32).itemsize
    batch_size = max(1, BATCH_BYTES // frame_bytes)
    total = None
    for t in range(0, T, batch_size):
        for frame in np.asarray(frames[t : t + batch_size], dtype=np.float32):
            if total is None:
                total = frame.copy()
            else:
                total += frame
    return np.true_divide(total, np.intp(T), out=total, casting="unsafe")


def session_rows(session_estimates, assignments, rows):
    """Union cell rows per session joined in time; NaN where assignments is NaN."""
    blocks = []
    for j, estimates in enumerate(session_estimates):
        matched = ~np.isnan(assignments[:, j])
        cells = rows(estimates, assignments[matched, j].astype(int))
        block = np.full((len(assignments), estimates.C.shape[1]), np.nan, cells.dtype)
        block[matched] = cells
        blocks.append(block)
    return np.concatenate(blocks, axis=1)


def session_fluorescence(session_estimates, assignments):
    return session_rows(session_estimates, assignments, lambda e, cells: e.C[cells])


def session_dff(session_estimates, assignments, frames_window):
    return session_rows(
        session_estimates,
        assignments,
        lambda e, cells: caiman_dff(e, cells, e.C.shape[1], frames_window),
    )


def union_footprints(spatial, assignments):
    """Each union cell's footprint from the first session it was matched in."""
    from scipy.sparse import hstack

    first = np.argmax(~np.isnan(assignments), axis=1)
    return hstack(
        [spatial[j][:, [int(assignments[i, j])]] for i, j in enumerate(first)],
        format="csc",
    )


def caiman_cnmf_multisession(
    images: ImageData, output_dir: str, params: dict = None, **kwargs
) -> dict(fluorescence=FluoData, dff=FluoData, iscell=IscellData, meanImg=ImageData):
    from caiman.base.rois import register_multisession
    from caiman.source_extraction.cnmf import cnmf
    from caiman.source_extraction.cnmf.params import CNMFParams

    function_id = ExptOutputPathIds(output_dir).function_id
    logger.info(f"start caiman_cnmf_multisession: {function_id}")

    # NOTE: evaluate_components requires cnn_model files in caiman_data directory.
    util_download_model_files()

    flattened_params = {}
    recursive_flatten_params(params or {}, flattened_params)
    params = parse_text_params(flattened_params)

    Ain = params.pop("Ain", None)
    roi_thr = params.pop("roi_thr", None)
    requested_n_processes = params.pop("n_processes", 1)
    frames_window = dff_frames_window(params.pop("frames_window", 500))

    # mulisiession params
    session_lengths = params.pop("session_lengths", None)
    n_reg_files = params.pop("n_reg_files", 2)
    reg_file_rate = params.pop("reg_file_rate", 1.0)
    if reg_file_rate > 1.0:
        logger.warning(
            f"reg_file_rate {reg_file_rate}, should be lte 1. Using 1.0 instead."
        )
        reg_file_rate = 1.0

    align_flag = params.pop("align_flag", True)
    max_thr = params.pop("max_thr", 0)
    use_opt_flow = params.pop("use_opt_flow", True)
    thresh_cost = params.pop("thresh_cost", 0.7)
    max_dist = params.pop("max_dist", 10)
    enclosed_thr = params.pop("enclosed_thr", None)

    if session_lengths:
        if n_reg_files != len(session_lengths):
            logger.warning(
                f"session_lengths gives {len(session_lengths)} parts; "
                f"n_reg_files {n_reg_files} is ignored."
            )
        split_image_paths = images.split_image(output_dir, lengths=session_lengths)
    else:
        if n_reg_files < 2:
            raise Exception(
                f"Set n_reg_files to a integer value gte 2. Now {n_reg_files}."
            )
        split_image_paths = images.split_image(output_dir, n_files=n_reg_files)
    n_split_images = len(split_image_paths)

    logger.info(f"image was split into {n_split_images} parts.")

    nwbfile = kwargs.get("nwbfile", {})
    fr = nwbfile.get("imaging_plane", {}).get("imaging_rate", 30)

    cnm_list = []
    templates = []
    mmap_paths = []
    with caiman_cluster(requested_n_processes) as (dview, n_processes):
        ops = CNMFParams()
        ops.change_params(distribute_params_to_groups({**params, "fr": fr}, vars(ops)))

        for split_image_path in split_image_paths:
            split_image_mmap, _, mmap_path = util_get_image_memmap_from_files(
                function_id, split_image_path, split_image_path
            )
            mmap_paths.append(mmap_path)

            cnm = cnmf.CNMF(n_processes=n_processes, dview=dview, Ain=Ain, params=ops)
            cnm = cnm.fit(split_image_mmap)
            cnm_list.append(cnm)
            templates.append(session_template(split_image_path))

            del split_image_mmap
            gc.collect()

    spatial = [cnm.estimates.A for cnm in cnm_list]
    dims = templates[0].shape

    spatial_union, assignments, matchings = register_multisession(
        A=spatial,
        dims=dims,
        templates=templates,
        align_flag=align_flag,
        max_thr=max_thr,
        use_opt_flow=use_opt_flow,
        thresh_cost=thresh_cost,
        max_dist=max_dist,
        enclosed_thr=enclosed_thr,
    )

    reg_files = int(len(split_image_paths) * reg_file_rate)
    kept = assignments[np.sum(~np.isnan(assignments), axis=1) >= reg_files]
    if len(kept) == 0:
        raise Exception(
            f"No same cells found in {reg_files} or more "
            + f"out of {n_split_images} files. "
            + "Try to set lower reg_file_rate.",
        )

    spatial_filtered = union_footprints(spatial, kept)

    session_estimates = [cnm.estimates for cnm in cnm_list]
    fluorescence = session_fluorescence(session_estimates, kept)
    dff = session_dff(session_estimates, kept, frames_window)

    # contours plot
    thr_method = "nrg"
    swap_dim = False

    iscell = np.ones(len(kept), dtype=int)

    # one footprint at a time: no full-frame layer per cell is kept
    roi_masks = RoiMasks.from_pixels(
        dims,
        (
            np.flatnonzero(mask)
            for mask in get_roi(spatial_filtered, roi_thr, thr_method, swap_dim, dims)
        ),
    )
    n_rois = len(roi_masks)
    all_roi = roi_masks.label_image()

    # NWBの追加
    nwbfile = {}
    # NWBにROIを追加
    roi_list = []
    n_cells = spatial_filtered.shape[-1]
    for i in range(n_cells):
        kargs = {}
        kargs["pixel_mask"] = nwb_pixel_mask(spatial_filtered, i, dims, swap_dim)
        roi_list.append(kargs)

    nwbfile[NWBDATASET.ROI] = {function_id: {"roi_list": roi_list}}
    nwbfile[NWBDATASET.POSTPROCESS] = {function_id: nwb_roi_label_image(all_roi)}

    # iscellを追加
    nwbfile[NWBDATASET.COLUMN] = {
        function_id: {
            "name": "iscell",
            "description": "iscell",
            "data": iscell,
        }
    }

    nwbfile[NWBDATASET.FLUORESCENCE] = {
        function_id: {
            "Fluorescence": {
                "table_name": "ROIs",
                "region": list(range(n_rois)),
                "name": "Fluorescence",
                "data": fluorescence,
                "unit": "lumens",
                "rate": fr,
                "comments": f"ROIs were detected in {reg_files} out of "
                + f"{n_split_images} sessions ({reg_file_rate} overall).",
            }
        }
    }

    # the parts hold the movie's frames, in its dtype and C order
    Cn, meanImg = correlation_image(FrameStack(split_image_paths))
    Cn[np.isnan(Cn)] = 0

    info = {
        "images": ImageData(
            np.array(Cn * 255, dtype=np.uint8),
            output_dir=output_dir,
            file_name="images",
        ),
        "meanImg": ImageData(
            meanImg.astype(np.float32), output_dir=output_dir, file_name="meanImg"
        ),
        "fluorescence": FluoData(fluorescence, file_name="fluorescence"),
        "dff": FluoData(dff, file_name="dff"),
        "iscell": IscellData(iscell, file_name="iscell"),
        "cell_roi": RoiData(
            roi_masks.label_image(iscell != 0),
            output_dir=output_dir,
            file_name="cell_roi",
        ),
        # ROI edits read new traces from a reference, opened at commit time: to
        # the input when it lies in this experiment, else to the parts, which do
        "edit_roi_data": EditRoiData(
            util_edit_roi_movie(
                images,
                lambda: edit_reference(ImageData(split_image_paths), output_dir),
                output_dir,
            ),
            roi_masks,
        ),
        "nwbfile": nwbfile,
    }

    # Clean up temporary files
    try:
        util_cleanup_image_memmap(mmap_paths)
    except Exception as e:
        logger.warning("Failed to cleanup memmap files.")
        logger.warning(e)

    return info
