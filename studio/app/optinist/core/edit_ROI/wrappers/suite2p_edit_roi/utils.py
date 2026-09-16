import os

import numpy as np
from scipy.ndimage import percentile_filter

from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import EditRoiData

# numpy ports of what suite2p's extraction does for a manually added ROI
# (create_cell_pix, extendROI, create_neuropil_masks, extract_traces), so a
# commit does not need the suite2p conda env.


def median_pix(ypix, xpix):
    ymed, xmed = np.median(ypix), np.median(xpix)
    imin = np.argmin((xpix - xmed) ** 2 + (ypix - ymed) ** 2)
    return [ypix[imin], xpix[imin]]


def get_stat0_add_roi(ops, posx, posy, sizex, sizey):
    posx, posy, sizex, sizey = round(posx), round(posy), round(sizex), round(sizey)
    xrange = (np.arange(-1 * int(sizex), 1) + int(posx)).astype(np.int32)
    yrange = (np.arange(-1 * int(sizey), 1) + int(posy)).astype(np.int32)
    xrange += int(np.floor(sizex / 2))
    yrange += int(np.floor(sizey / 2))

    x, y = np.meshgrid(np.arange(0, xrange.size, 1), np.arange(0, yrange.size, 1))
    ellipse = (
        (y - sizey / 2) ** 2 / (sizey / 2) ** 2
        + (x - sizex / 2) ** 2 / (sizex / 2) ** 2
    ) <= 1

    ellipse = ellipse[:, np.logical_and(xrange >= 0, xrange < ops["Lx"])]
    xrange = xrange[np.logical_and(xrange >= 0, xrange < ops["Lx"])]
    ellipse = ellipse[np.logical_and(yrange >= 0, yrange < ops["Ly"]), :]
    yrange = yrange[np.logical_and(yrange >= 0, yrange < ops["Ly"])]

    x, y = np.meshgrid(xrange, yrange)
    ypix = y[ellipse].flatten()
    xpix = x[ellipse].flatten()
    return {"ypix": ypix, "xpix": xpix, "lam": np.ones(ypix.shape, np.float32)}


def merge_stat(stat, ids):
    merged_cells = np.unique(np.array(ids))
    ypix = np.concatenate([stat[n]["ypix"] for n in merged_cells])
    xpix = np.concatenate([stat[n]["xpix"] for n in merged_cells])
    lam = np.concatenate([stat[n]["lam"] for n in merged_cells])

    # remove overlaps from merged cells regions
    _, goodi = np.unique(np.stack((ypix, xpix), axis=1), return_index=True, axis=0)
    lam = lam[goodi]
    return {
        "ypix": ypix[goodi],
        "xpix": xpix[goodi],
        "lam": lam / lam.sum() * merged_cells.size,
        "chan2_prob": -1,
        "inmerge": -1,
    }


def roi_diameter(ops):
    if "aspect" in ops:
        return int(ops["aspect"] * 10), 10
    d0 = ops["diameter"]
    return (d0, d0) if isinstance(d0, int) else tuple(d0)


def soma_crop(ypix, xpix, lam, med):
    if ypix.size <= 10:
        return np.ones(ypix.size, bool)
    dists = ((ypix - med[0]) ** 2 + (xpix - med[1]) ** 2) ** 0.5
    radii = np.arange(0, dists.max(), 1)
    area = np.array([lam[dists < r].sum() for r in radii])
    darea = np.diff(area)
    radius = radii[-1]
    threshold = darea.max() / 3
    above = np.nonzero(darea > threshold)[0]
    if above.size:
        below = np.nonzero(darea[above[0] :] < threshold)[0]
        if below.size:
            radius = radii[below[0] + above[0]]
    crop = dists < radius
    return crop if crop.sum() else np.ones(ypix.size, bool)


def ellipse_radius(ypix, xpix, lam, med, dy, dx, thres=2):
    """Semi-major axis of the 2-sigma gaussian fit to the soma crop, in pixels."""
    crop = soma_crop(ypix, xpix, lam, med)
    y, x, lam = ypix[crop] / dy, xpix[crop] / dx, lam[crop].astype(float)
    positive = lam > 0
    y, x, lam = y[positive], x[positive], lam[positive]
    lam = lam / lam.sum()
    yx = np.stack((y, x))
    mu = (lam * yx).sum(axis=1)
    yx = (yx - mu[:, np.newaxis]) * lam**0.5
    radii = thres * np.maximum(0, np.real(np.linalg.eigvals(yx @ yx.T))) ** 0.5
    return float(radii.max() * np.mean((dx, dy)))


def complete_stat(stat0, stat, ops):
    """Fill the fields read on a new ROI, leaving the existing ones untouched."""
    Ly, Lx = ops["Ly"], ops["Lx"]
    occupied = np.zeros((Ly, Lx), bool)
    for s in stat:
        occupied[s["ypix"], s["xpix"]] = True
    ypix, xpix, lam = stat0["ypix"], stat0["xpix"], stat0["lam"]
    stat0["med"] = median_pix(ypix, xpix)
    stat0["npix"] = ypix.size
    stat0["overlap"] = occupied[ypix, xpix]
    stat0["radius"] = ellipse_radius(ypix, xpix, lam, stat0["med"], *roi_diameter(ops))
    return stat0


def extend_roi(ypix, xpix, Ly, Lx, niter):
    for _ in range(niter):
        yx = np.array(
            (
                (ypix, ypix, ypix, ypix - 1, ypix + 1),
                (xpix, xpix + 1, xpix - 1, xpix, xpix),
            )
        ).reshape(2, -1)
        yu = np.unique(yx, axis=1)
        inside = np.all((yu[0] >= 0, yu[0] < Ly, yu[1] >= 0, yu[1] < Lx), axis=0)
        ypix, xpix = yu[:, inside]
    return ypix, xpix


def cell_pix_map(stat, Ly, Lx, lam_percentile=50.0):
    lammap = np.zeros((Ly, Lx))
    for s in stat:
        lammap[s["ypix"], s["xpix"]] = np.maximum(
            lammap[s["ypix"], s["xpix"]], s["lam"]
        )
    radius = np.median([s["radius"] for s in stat])
    filt = percentile_filter(lammap, percentile=lam_percentile, size=int(radius * 5))
    return ~np.logical_or(lammap < filt, lammap == 0)


def neuropil_pixels(ypix, xpix, cell_pix, inner_radius, min_pixels, extend_by=5):
    Ly, Lx = cell_pix.shape
    ypix, xpix = extend_roi(ypix, xpix, Ly, Lx, inner_radius)
    nring = np.sum(~cell_pix[ypix, xpix])
    ypix1, xpix1 = ypix, xpix
    for _ in range(100):
        if np.sum(~cell_pix[ypix1, xpix1]) - nring > min_pixels:
            break
        ypix1, xpix1 = np.meshgrid(
            np.arange(
                max(0, ypix1.min() - extend_by), min(Ly, ypix1.max() + extend_by + 1)
            ),
            np.arange(
                max(0, xpix1.min() - extend_by), min(Lx, xpix1.max() + extend_by + 1)
            ),
            indexing="ij",
        )
    mask = np.zeros((Ly, Lx), bool)
    mask[ypix1, xpix1] = ~cell_pix[ypix1, xpix1]
    mask[ypix, xpix] = False
    return np.nonzero(mask)


def extract_traces(ops, stat, targets):
    """F and Fneu of `targets` from the registered binary; `stat` holds every ROI."""
    Ly, Lx = ops["Ly"], ops["Lx"]
    reg_file = ops["reg_file"]
    if not os.path.exists(reg_file):
        raise FileNotFoundError(f"registered movie not found: {reg_file}")
    nframes = os.path.getsize(reg_file) // (Ly * Lx * 2)
    mov = np.memmap(reg_file, dtype=np.int16, mode="r", shape=(nframes, Ly, Lx))
    cell_pix = cell_pix_map(stat, Ly, Lx)

    F = np.zeros((len(targets), nframes), np.float32)
    Fneu = np.zeros_like(F)
    for i, s in enumerate(targets):
        keep = slice(None) if ops.get("allow_overlap", False) else ~s["overlap"]
        lam = s["lam"][keep].astype(np.float32)
        if lam.size > 0:
            pixels = mov[:, s["ypix"][keep], s["xpix"][keep]].astype(np.float32)
            F[i] = pixels @ (lam / lam.sum())
        ny, nx = neuropil_pixels(
            s["ypix"],
            s["xpix"],
            cell_pix,
            ops.get("inner_neuropil_radius", 2),
            ops.get("min_neuropil_pixels", 350),
        )
        Fneu[i] = mov[:, ny, nx].astype(np.float32).mean(axis=1)
    return F, Fneu


def set_nwbfile(ops, iscell, edit_roi_data: EditRoiData, function_id):
    stat = ops.get("stat", [])  # Default to empty
    F = ops.get("F", np.zeros((1, 1)))  # Default to minimum
    Fneu = ops.get("Fneu", np.zeros((1, 1)))

    roi_list = []
    if len(stat) == 0:
        # Add a dummy ROI entry if empty to maintain table structure
        roi_list.append(
            {"pixel_mask": np.zeros((1, 3))}  # Empty pixel mask (x,y,weight)
        )
        iscell = np.array([[0, 0]])  # [iscell, probcell]
    else:
        for i in range(len(stat)):
            kargs = {}
            kargs["pixel_mask"] = np.array(
                [stat[i]["ypix"], stat[i]["xpix"], stat[i]["lam"]]
            ).T
            roi_list.append(kargs)
    nwbfile = {}

    nwbfile[NWBDATASET.ROI] = {function_id: {"roi_list": roi_list}}

    # iscellを追加
    nwbfile[NWBDATASET.COLUMN] = {
        function_id: {
            "name": "iscell",
            "description": "two columns - iscell & probcell",
            "data": iscell if iscell.size > 0 else np.array([[0, 0]]),
        }
    }

    # Fluorenceを追加
    nwbfile[NWBDATASET.FLUORESCENCE] = {
        function_id: {
            "Fluorescence": {
                "table_name": "Fluorescence",
                "region": list(range(F.shape[0])),
                "name": "Fluorescence",
                "data": F if F.size > 0 else np.array([]).reshape(0, 0),
                "unit": "lumens",
                "rate": ops["fs"],
            },
            "Neuropil": {
                "table_name": "Neuropil",
                "region": list(range(Fneu.shape[0])),
                "name": "Neuropil",
                "data": Fneu if Fneu.size > 0 else np.array([]).reshape(0, 0),
                "unit": "lumens",
                "rate": ops["fs"],
            },
        }
    }

    # NWB追加
    nwbfile[NWBDATASET.POSTPROCESS] = {
        function_id: {
            "add_roi": edit_roi_data.add_roi,
            "delete_roi": edit_roi_data.delete_roi,
            "merge_roi": edit_roi_data.merge_roi,
        }
    }

    return nwbfile
