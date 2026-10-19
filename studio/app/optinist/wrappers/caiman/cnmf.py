import contextlib
import gc
import os

import numpy as np
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.utils.filepath_creater import (
    create_directory,
    join_filepath,
)
from studio.app.common.dataclass import ImageData
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import EditRoiData, FluoData, IscellData, RoiData
from studio.app.optinist.dataclass.roi import edit_reference
from studio.app.optinist.wrappers.caiman.caiman_utils import (
    caiman_cluster,
    distribute_params_to_groups,
    parse_text_params,
)
from studio.app.optinist.wrappers.optinist.utils import recursive_flatten_params

logger = AppLogger.get_logger()

CORRELATION_CHUNK_ELEMENTS = 2**24  # float64 values per frame chunk, 128 MB


def get_roi(A, roi_thr, thr_method, swap_dim, dims):
    from scipy.ndimage import binary_fill_holes
    from skimage.measure import find_contours

    d, nr = np.shape(A)

    # for each patches
    ims = []
    coordinates = []
    for i in range(nr):
        pars = dict()
        # we compute the cumulative sum of the energy of the Ath component
        # that has been ordered from least to highest
        patch_data = A.data[A.indptr[i] : A.indptr[i + 1]]
        idx = np.argsort(patch_data)[::-1]

        if thr_method == "nrg":
            cumEn = np.cumsum(patch_data[idx] ** 2)
            if len(cumEn) == 0:
                pars = dict(
                    coordinates=np.array([]),
                    CoM=np.array([np.NaN, np.NaN]),
                    neuron_id=i + 1,
                )
                coordinates.append(pars)
                # keep one mask per column so row i stays component i
                ims.append(np.zeros(dims))
                continue
            else:
                # we work with normalized values
                cumEn /= cumEn[-1]
                Bvec = np.ones(d)
                # we put it in a similar matrix
                Bvec[A.indices[A.indptr[i] : A.indptr[i + 1]][idx]] = cumEn
        else:
            Bvec = np.zeros(d)
            Bvec[A.indices[A.indptr[i] : A.indptr[i + 1]]] = (
                patch_data / patch_data.max()
            )

        if swap_dim:
            Bmat = np.reshape(Bvec, dims, order="C")
        else:
            Bmat = np.reshape(Bvec, dims, order="F")

        r_mask = np.zeros_like(Bmat, dtype="bool")
        contour = find_contours(Bmat, roi_thr)
        for c in contour:
            r_mask[np.round(c[:, 0]).astype("int"), np.round(c[:, 1]).astype("int")] = 1

        # Fill in the hole created by the contour boundary
        r_mask = binary_fill_holes(r_mask)
        ims.append(r_mask + (i * r_mask))

    return ims


def util_get_image_memmap(function_id: str, images: np.ndarray, file_path: str):
    """
    convert np.ndarray to mmap
    """
    from caiman.mmapping import prepare_shape
    from caiman.paths import memmap_frames_filename

    order = "C"
    dims = images.shape[1:]
    T = images.shape[0]
    shape_mov = (np.prod(dims), T)

    dir_path = join_filepath(file_path.split("/")[:-1])
    file_basename = file_path.split("/")[-1]
    mmap_basename = f"{file_basename}.{function_id}"
    fname_tot = memmap_frames_filename(mmap_basename, dims, T, order)
    mmap_path = join_filepath([dir_path, fname_tot])

    mmap_images = np.memmap(
        mmap_path,
        mode="w+",
        dtype=np.float32,
        shape=prepare_shape(shape_mov),
        order=order,
    )

    mmap_images = np.reshape(mmap_images.T, [T] + list(dims), order="F")
    mmap_images[:] = images[:]
    return mmap_images, dims, mmap_path


def correlation_image(movie):
    """caiman.local_correlations(movie.transpose(1, 2, 0)) for a (T, d1, d2) movie.

    The moments are accumulated in float64 over frame chunks, so memory stays near
    one chunk instead of the several whole-movie float32 copies caiman makes.
    """
    T, d1, d2 = movie.shape
    step = max(1, CORRELATION_CHUNK_ELEMENTS // (d1 * d2))
    chunks = [movie[t : t + step] for t in range(0, T, step)]

    mean = sum(c.sum(axis=0, dtype=np.float64) for c in chunks) / T
    # caiman's rho_h, rho_w, rho_d1, rho_d2 pixel pairs
    pairs = [
        (np.s_[:-1, :], np.s_[1:, :]),
        (np.s_[:, :-1], np.s_[:, 1:]),
        (np.s_[1:, :-1], np.s_[:-1, 1:]),
        (np.s_[:-1, :-1], np.s_[1:, 1:]),
    ]
    sq = np.zeros((d1, d2))
    cross = [0.0] * len(pairs)
    for c in chunks:
        d = c - mean
        sq += (d * d).sum(axis=0)
        for i, (a, b) in enumerate(pairs):
            cross[i] = cross[i] + (d[:, a[0], a[1]] * d[:, b[0], b[1]]).sum(axis=0)
    std = np.sqrt(sq / T)
    rho_h, rho_w, rho_d1, rho_d2 = (
        x / T / (std[a] * std[b]) for x, (a, b) in zip(cross, pairs)
    )

    # caiman's placement, swapped diagonal terms included, so the image is unchanged
    rho = np.zeros((d1, d2))
    rho[:-1, :] += rho_h
    rho[1:, :] += rho_h
    rho[:, :-1] += rho_w
    rho[:, 1:] += rho_w
    rho[:-1, :-1] += rho_d2
    rho[1:, 1:] += rho_d1
    rho[1:, :-1] += rho_d1
    rho[:-1, 1:] += rho_d2
    neighbors = np.full((d1, d2), 8.0)
    neighbors[[0, -1], :] -= 3
    neighbors[:, [0, -1]] -= 3
    neighbors[[0, 0, -1, -1], [0, -1, 0, -1]] += 1
    return rho / neighbors


def edit_roi_dtype(dtype):
    """The movie kept for ROI edits: its own dtype when float32 holds it exactly."""
    return dtype if np.can_cast(dtype, np.float32) else np.float32


def util_get_image_memmap_from_files(
    function_id: str, paths, file_path: str, batch_size: int = 1024
):
    """
    As util_get_image_memmap(function_id, ImageData(paths).data, file_path), but
    filled from the image files batch by batch, with the same values: the movie
    is not held in memory as a whole, nor a second time while being copied into
    the memmap. A file is memory-mapped when its pixels are contiguous, else
    decoded a batch of pages at a time when each page is one frame (compressed
    stacks), and read in full only otherwise.

    The memmap is pixels x time, so a 4 KiB page holds 1024 consecutive frames
    of one pixel: batches of about that many frames write each page about
    once (smaller batches rewrite every page several times and run slower).
    """
    import imageio
    import tifffile
    from caiman.mmapping import prepare_shape
    from caiman.paths import memmap_frames_filename

    paths = paths if isinstance(paths, list) else [paths]

    def header_shape(path):
        try:
            with tifffile.TiffFile(path) as tif:
                return tuple(int(n) for n in tif.series[0].shape)
        except Exception:
            return np.asarray(imageio.volread(path)).shape

    def frame_batches(path, frames):
        try:
            source = tifffile.memmap(path, mode="r")
        except ValueError:  # compressed or scattered pixels, or not a TIFF
            source = None
        if source is not None:
            for i in range(0, len(source), batch_size):
                yield source[i : i + batch_size]
            return
        try:
            with tifffile.TiffFile(path) as tif:
                series = tif.series[0]
                if len(series.pages) == frames:  # one page per frame
                    for i in range(0, frames, batch_size):
                        pages = range(i, min(i + batch_size, frames))
                        yield tif.asarray(key=pages, series=0).reshape(
                            (len(pages),) + series.shape[1:]
                        )
                    return
        except tifffile.TiffFileError:
            pass
        logger.info("%s is read in full to fill the memmap", os.path.basename(path))
        yield np.asarray(imageio.volread(path))

    shapes = [header_shape(path) for path in paths]
    T, dims = sum(s[0] for s in shapes), shapes[0][1:]

    # The per-file assignment below would broadcast a mismatched frame shape
    # (a 2D file, or a (T, 1, X) one) where concatenating the files raises
    for path, shape in zip(paths, shapes):
        name = os.path.basename(path)
        if len(shape) < 3:
            raise ValueError(f"{name}: shape {shape} is not a stack of frames")
        if shape[1:] != dims:
            raise ValueError(
                f"{name}: frame shape {shape[1:]} differs from {dims}, "
                f"the frame shape of {os.path.basename(paths[0])}"
            )

    order = "C"
    shape_mov = (np.prod(dims), T)
    dir_path = join_filepath(file_path.split("/")[:-1])
    file_basename = file_path.split("/")[-1]
    mmap_basename = f"{file_basename}.{function_id}"
    fname_tot = memmap_frames_filename(mmap_basename, dims, T, order)
    mmap_path = join_filepath([dir_path, fname_tot])

    mmap = np.memmap(
        mmap_path,
        mode="w+",
        dtype=np.float32,
        shape=prepare_shape(shape_mov),
        order=order,
    )
    mmap_images = np.reshape(mmap.T, [T] + list(dims), order="F")

    try:
        t = 0
        for path, shape in zip(paths, shapes):
            end = t + shape[0]
            for batch in frame_batches(path, shape[0]):
                if batch.shape[1:] != dims or t + len(batch) > end:
                    raise ValueError(
                        f"{os.path.basename(path)}: read as frames of "
                        f"{batch.shape[1:]}, but its headers give {shape}"
                    )
                mmap_images[t : t + len(batch)] = batch
                t += len(batch)
            if t != end:
                raise ValueError(
                    f"{os.path.basename(path)}: read {shape[0] - (end - t)} "
                    f"frames, but its headers give {shape[0]}"
                )
    except BaseException:
        # no half-filled memmap left for the run to use; a failure to remove it
        # must not hide why the fill failed
        with contextlib.suppress(OSError):
            os.remove(mmap_path)
        raise
    return mmap_images, dims, mmap_path


def util_edit_roi_movie(images: ImageData, fallback, output_dir: str):
    """
    The movie EditRoiData keeps for ROI edits: a reference to the input when it
    lives in this node's experiment (see edit_reference), else the fallback copy,
    or what a fallback function returns, called only when there is no reference.
    """
    reference = edit_reference(images, output_dir)
    if reference is not None:
        return reference
    return fallback() if callable(fallback) else fallback


def util_cleanup_image_memmap(mmap_paths: list):
    for mmap_path in mmap_paths:
        if mmap_path.endswith(".mmap") and os.path.isfile(mmap_path):
            os.remove(mmap_path)


def util_download_model_files():
    """
    download model files for component evaluation
    """
    # NOTE: We specify the version of the CaImAn to download.
    base_url = (
        "https://raw.githubusercontent.com/flatironinstitute/CaImAn/v1.9.12/model"
    )
    model_files = [
        "cnn_model.h5",
        "cnn_model.h5.pb",
        "cnn_model.json",
        "cnn_model_online.h5",
        "cnn_model_online.h5.pb",
        "cnn_model_online.json",
    ]

    caiman_data_dir = os.path.join(os.path.expanduser("~"), "caiman_data")
    if not os.path.exists(caiman_data_dir):
        create_directory(caiman_data_dir)

    model_dir = join_filepath([caiman_data_dir, "model"])
    if not os.path.exists(model_dir):
        create_directory(join_filepath(model_dir))

    retry = Retry(total=5, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))

    for model in model_files:
        url = f"{base_url}/{model}"
        file_path = join_filepath([model_dir, model])
        if not os.path.exists(file_path):
            logger.info(f"Downloading {model}")
            response = session.get(url, timeout=30)
            response.raise_for_status()
            with open(file_path, "wb") as f:
                f.write(response.content)


def component_outputs(
    A,
    C,
    idx_good,
    idx_bad,
    dims,
    roi_thr,
    thr_method,
    swap_dim,
    accepted_list=None,
    rejected_list=None,
    n_frames=0,
):
    """im, iscell, the NWB ROI table and fluorescence in one shared row order.

    Row i of every output is component order[i], accepted components first, so
    the ROI editor and the analyses can index fluorescence by im row. order is
    returned last for the outputs computed elsewhere.
    """
    order = list(idx_good) + list(idx_bad)
    n_components = len(order)

    iscell = np.concatenate(
        [
            np.ones(len(idx_good), dtype=int),
            np.zeros(len(idx_bad), dtype=int),
        ]
    )

    if len(idx_good) > 0 and A is not None:
        cell_ims = get_roi(A[:, idx_good], roi_thr, thr_method, swap_dim, dims)
        if len(cell_ims) != len(idx_good):
            raise ValueError("one mask per accepted component expected")
        if len(cell_ims) > 0:  # Check if get_roi returned any ROIs
            cell_ims = np.stack(cell_ims).astype(float)
            cell_ims[cell_ims == 0] = np.nan
            cell_ims = np.where(
                np.isnan(cell_ims), cell_ims, cell_ims - 1
            )  # Safer subtraction
            n_rois = len(cell_ims)
        else:
            cell_ims = np.zeros((0, *dims))
            n_rois = 0
    else:
        cell_ims = np.zeros((0, *dims))
        n_rois = 0

    if len(idx_bad) > 0:
        non_cell_ims = get_roi(A[:, idx_bad], roi_thr, thr_method, swap_dim, dims)
        if len(non_cell_ims) != len(idx_bad):
            raise ValueError("one mask per rejected component expected")
        non_cell_ims = np.stack(non_cell_ims).astype(float)
        for i, j in enumerate(range(n_rois, n_rois + len(non_cell_ims))):
            non_cell_ims[i, :] = np.where(non_cell_ims[i, :] != 0, j, 0)
        non_cell_roi = np.nanmax(non_cell_ims, axis=0).astype(float)
    else:
        non_cell_ims = np.zeros((0, *dims))
        non_cell_roi = np.zeros(dims)
        non_cell_roi[non_cell_roi == 0] = np.nan
    non_cell_ims[non_cell_ims == 0] = np.nan

    n_noncell_rois = len(non_cell_ims)

    im = (
        np.vstack([cell_ims, non_cell_ims])
        if n_components > 0
        else np.zeros((0, *dims))
    )

    # the pixel order get_roi reads A in, so the NWB masks match the ROI images
    mask_order = "C" if swap_dim else "F"
    roi_list = []
    for comp in order:
        kargs = {}
        kargs["image_mask"] = A[:, comp].toarray().reshape(dims, order=mask_order)
        if accepted_list is not None:
            kargs["accepted"] = comp in accepted_list
        if rejected_list is not None:
            kargs["rejected"] = comp in rejected_list
        roi_list.append(kargs)

    fluorescence = (
        np.asarray(C)[order]
        if n_components > 0 and C is not None
        else np.zeros((0, n_frames))
    )

    return (
        im,
        iscell,
        roi_list,
        fluorescence,
        non_cell_roi,
        n_rois,
        n_noncell_rois,
        order,
    )


def dff_frames_window(value):
    """frames_window for detrend_df_f: None (whole recording) or an integer >= 2."""
    if value is None:
        return None
    try:
        window = int(value)
    except (TypeError, ValueError):
        window = None
    if isinstance(value, bool) or window is None or window != value or window < 2:
        raise ValueError(
            f"frames_window must be null or an integer of at least 2, got {value!r}"
        )
    return window


def caiman_dff(estimates, rows, n_frames, frames_window):
    """CaImAn's detrend_df_f of estimates as float32, rows in the given order.

    A CaImAn failure gives NaN rows with a warning instead of failing the node.
    """
    if len(rows) == 0:
        return np.zeros((0, n_frames), dtype=np.float32)
    from caiman.source_extraction.cnmf.utilities import detrend_df_f

    b, f = estimates.b, estimates.f
    if b is None or f is None or np.shape(b)[-1] == 0:
        # detrend_df_f only takes None as no background; zero-width divides by F0
        b = f = None
    b0 = getattr(estimates, "b0", None)
    if b is None and b0 is not None:
        # ring model: the static background b0 only, its W fluctuations left out
        b, f = np.reshape(b0, (-1, 1)), np.ones((1, n_frames))
    elif b is None:
        logger.warning(
            "CaImAn estimates have no background (b, f or b0); dff is "
            "detrended only, F - F0, not divided by a baseline"
        )
    try:
        dff = detrend_df_f(
            estimates.A,
            b,
            estimates.C,
            f,
            YrA=estimates.YrA,
            frames_window=frames_window,
            flag_auto=True,
            use_fast=False,
        )
    except Exception:
        logger.warning("CaImAn detrend_df_f failed, dff is all NaN", exc_info=True)
        return np.full((len(rows), n_frames), np.nan, dtype=np.float32)
    return np.asarray(dff, dtype=np.float32)[rows]


def caiman_cnmf(
    images: ImageData, output_dir: str, params: dict = None, **kwargs
) -> dict(fluorescence=FluoData, dff=FluoData, iscell=IscellData):
    from caiman.source_extraction.cnmf import cnmf, online_cnmf
    from caiman.source_extraction.cnmf.params import CNMFParams

    function_id = ExptOutputPathIds(output_dir).function_id
    logger.info(f"start caiman_cnmf: {function_id}")

    # ROI editing reads new traces from the input movie, opened lazily at
    # commit time: EditRoiData keeps a reference to it when util_edit_roi_movie
    # allows it, rather than a copy
    images_ref = images

    # NOTE: evaluate_components requires cnn_model files in caiman_data directory.
    util_download_model_files()

    flattened_params = {}
    recursive_flatten_params(params or {}, flattened_params)
    params = parse_text_params(flattened_params)

    Ain = params.pop("Ain", None)
    do_refit = params.pop("do_refit", None)
    roi_thr = params.pop("roi_thr", None)
    use_online = params.pop("use_online", False)
    requested_n_processes = params.pop("n_processes", 1)
    frames_window = dff_frames_window(params.pop("frames_window", 500))

    file_path = images.path
    if isinstance(file_path, list):
        file_path = file_path[0]

    mmap_paths = []
    mmap_images, dims, mmap_path = util_get_image_memmap_from_files(
        function_id, images.path, file_path
    )
    mmap_paths.append(mmap_path)

    del images
    gc.collect()

    nwbfile = kwargs.get("nwbfile", {})
    fr = nwbfile.get("imaging_plane", {}).get("imaging_rate", 30)

    with caiman_cluster(requested_n_processes) as (dview, n_processes):
        ops = CNMFParams()
        ops.change_params(distribute_params_to_groups({**params, "fr": fr}, vars(ops)))

        if use_online:
            ops.change_params(
                {
                    "data": {"fnames": [mmap_path]},
                    # NOTE: These params uses np.inf as default in CaImAn.
                    # Yaml cannot serialize np.inf, so default value in yaml is None.
                    "online": {
                        "max_comp_update_shape": params["max_comp_update_shape"]
                        or np.inf,
                        "num_times_comp_updated": params["num_times_comp_updated"]
                        or np.inf,
                    },
                }
            )
            cnm = online_cnmf.OnACID(dview=dview, Ain=Ain, params=ops)
            cnm.fit_online()
        else:
            cnm = cnmf.CNMF(n_processes=n_processes, dview=dview, Ain=Ain, params=ops)
            cnm = cnm.fit(mmap_images)

            if do_refit:
                cnm = cnm.refit(mmap_images, dview=dview)

        # Check if any components were found
        n_components = cnm.estimates.A.shape[1] if hasattr(cnm.estimates, "A") else 0

        if n_components > 0:
            # Only evaluate components if we found some
            cnm.estimates.evaluate_components(mmap_images, cnm.params, dview=dview)
            idx_good = cnm.estimates.idx_components
            idx_bad = cnm.estimates.idx_components_bad
            if not isinstance(idx_good, list):
                idx_good = idx_good.tolist()
            if not isinstance(idx_bad, list):
                idx_bad = idx_bad.tolist()
        else:
            # No components found
            idx_good = []
            idx_bad = []

    # contours plot
    Cn = correlation_image(mmap_images)
    Cn[np.isnan(Cn)] = 0

    thr_method = "nrg"
    swap_dim = False

    (
        im,
        iscell,
        roi_list,
        fluorescence,
        non_cell_roi,
        n_rois,
        n_noncell_rois,
        order,
    ) = component_outputs(
        getattr(cnm.estimates, "A", None),
        getattr(cnm.estimates, "C", None),
        idx_good,
        idx_bad,
        dims,
        roi_thr,
        thr_method,
        swap_dim,
        getattr(cnm.estimates, "accepted_list", None),
        getattr(cnm.estimates, "rejected_list", None),
        mmap_images.shape[0],
    )
    dff = caiman_dff(cnm.estimates, order, fluorescence.shape[1], frames_window)

    # NWB additions
    nwbfile = {}
    nwbfile[NWBDATASET.ROI] = {function_id: {"roi_list": roi_list}}
    nwbfile[NWBDATASET.POSTPROCESS] = {function_id: {"all_roi_img": im}}

    # Add iscell to NWB
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
                "table_name": "ROIs",
                "region": list(range(n_rois + n_noncell_rois)),
                "name": "Fluorescence",
                "data": fluorescence,
                "unit": "lumens",
                "rate": fr,
            }
        }
    }

    # Create empty ROI array filled with NaN for when no components found
    empty_roi = np.full(dims, np.nan)

    info = {
        "images": ImageData(
            np.array(Cn * 255, dtype=np.uint8),
            output_dir=output_dir,
            file_name="images",
        ),
        "fluorescence": FluoData(fluorescence, file_name="fluorescence"),
        "dff": FluoData(dff, file_name="dff"),
        "iscell": IscellData(iscell, file_name="iscell"),
        "all_roi": RoiData(
            np.nanmax(im, axis=0) if len(im) > 0 else empty_roi.copy(),
            output_dir=output_dir,
            file_name="all_roi",
        ),
        "cell_roi": RoiData(
            np.nanmax(im[iscell != 0], axis=0) if len(im) > 0 else empty_roi.copy(),
            output_dir=output_dir,
            file_name="cell_roi",
        ),
        "non_cell_roi": RoiData(
            non_cell_roi if len(im) > 0 else empty_roi.copy(),
            output_dir=output_dir,
            file_name="non_cell_roi",
        ),
        "edit_roi_data": EditRoiData(
            util_edit_roi_movie(images_ref, mmap_images, output_dir), im
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
