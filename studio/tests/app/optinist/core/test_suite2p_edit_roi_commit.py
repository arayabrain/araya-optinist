import numpy as np

from studio.app.optinist.core.edit_ROI.edit_ROI import CellType
from studio.app.optinist.core.edit_ROI.utils import create_ellipse_mask
from studio.app.optinist.core.edit_ROI.wrappers.suite2p_edit_roi import commit_edit
from studio.app.optinist.dataclass import EditRoiData, Suite2pData
from studio.app.optinist.schemas.roi import RoiPos

LY = LX = 40
FRAMES = 6
# frame t is flat at 100 + 10 t, so any mean over any pixel set is 100 + 10 t
FLAT = (np.arange(FRAMES) * 10 + 100).astype(np.int16)
CELLS = [(8, 8), (30, 22)]  # the second one sits inside the new ROI's neuropil box
NEW_ROI = RoiPos(posx=30, posy=30, sizex=6, sizey=6)


def disk(cy, cx, r):
    yy, xx = np.mgrid[:LY, :LX]
    return np.nonzero((yy - cy) ** 2 + (xx - cx) ** 2 <= r * r)


def build(tmp_path, movie):
    reg_file = tmp_path / "data.bin"
    movie.astype(np.int16).tofile(reg_file)
    stat, im = [], []
    for i, (cy, cx) in enumerate(CELLS):
        ypix, xpix = disk(cy, cx, 3)
        stat.append(
            {
                "ypix": ypix,
                "xpix": xpix,
                "lam": np.ones(ypix.size, np.float32),
                "overlap": np.zeros(ypix.size, bool),
                "radius": 3.0,
                "med": [cy, cx],
                "npix": ypix.size,
            }
        )
        plane = np.full((LY, LX), np.nan)
        plane[ypix, xpix] = i
        im.append(plane)
    ops = {
        "Ly": LY,
        "Lx": LX,
        "reg_file": str(reg_file),
        "fs": 30.0,
        "diameter": 10,
        "allow_overlap": False,
        "inner_neuropil_radius": 2,
        "min_neuropil_pixels": 20,
        "stat": stat,
        "F": np.tile(np.array([[1.0], [3.0]], np.float32), (1, FRAMES)),
        "Fneu": np.tile(np.array([[10.0], [30.0]], np.float32), (1, FRAMES)),
    }
    return ops, np.stack(im)


def test_add_extracts_trace_and_neuropil_from_the_registered_movie(tmp_path):
    movie = np.broadcast_to(FLAT[:, None, None], (FRAMES, LY, LX)).copy()
    roi_y, roi_x = disk(NEW_ROI.posy, NEW_ROI.posx, 3)
    movie[:, roi_y, roi_x] += 500
    # a bright neighbouring cell must not leak into the new ROI's neuropil
    cell_y, cell_x = disk(*CELLS[1], 3)
    movie[:, cell_y, cell_x] += 5000
    ops, im = build(tmp_path, movie)
    data = EditRoiData(
        images=None, im=np.vstack((im, create_ellipse_mask((LY, LX), NEW_ROI)[None]))
    )
    data.temp_add_roi[2] = NEW_ROI
    iscell = np.array([CellType.ROI, CellType.ROI, CellType.TEMP_ADD])

    info = commit_edit(data, Suite2pData(ops), iscell, str(tmp_path), "suite2p_roi_t")

    ops = info["ops"].data
    assert ops["F"].shape == ops["Fneu"].shape == (3, FRAMES)
    # a float32 weighted sum over 29 pixels is not bit-exact against an integer
    assert np.allclose(ops["F"][2], FLAT + 500, atol=1e-2)
    assert np.array_equal(ops["Fneu"][2], FLAT)
    assert info["fluorescence"].data.shape == (3, FRAMES)
    assert list(info["iscell"].data) == [CellType.ROI] * 3
    new = ops["stat"][2]
    assert set(new) >= {"ypix", "xpix", "lam", "overlap", "radius", "med", "npix"}
    assert new["npix"] == roi_y.size and not new["overlap"].any()
    # suite2p's soma crop keeps the 9 pixels within distance 2 of the centre,
    # whose coordinate variance is 2/3; the 2-sigma fit radius follows
    assert np.isclose(new["radius"], 2 * np.sqrt(2 / 3))
    assert data.add_roi == [2]


def test_merge_averages_traces_and_unions_pixels(tmp_path):
    ops, im = build(tmp_path, np.broadcast_to(FLAT[:, None, None], (FRAMES, LY, LX)))
    ops["reg_file"] = str(tmp_path / "missing.bin")  # a merge reads no movie
    data = EditRoiData(images=None, im=np.vstack((im, np.nanmax(im, axis=0)[None])))
    data.temp_merge_roi[2.0] = [0, 1]
    iscell = np.array([CellType.TEMP_DELETE, CellType.TEMP_DELETE, CellType.TEMP_ADD])

    info = commit_edit(data, Suite2pData(ops), iscell, str(tmp_path), "suite2p_roi_t")

    ops = info["ops"].data
    assert np.array_equal(ops["F"][2], np.full(FRAMES, 2.0))
    assert np.array_equal(ops["Fneu"][2], np.full(FRAMES, 20.0))
    merged = ops["stat"][2]
    assert merged["npix"] == ops["stat"][0]["npix"] + ops["stat"][1]["npix"]
    assert np.isclose(merged["lam"].sum(), 2.0)
    assert merged["overlap"].all()
    assert list(info["iscell"].data) == [
        CellType.NON_ROI,
        CellType.NON_ROI,
        CellType.ROI,
    ]
    assert data.merge_roi == [2.0, 0, 1, -1.0]


def test_delete_only_touches_flags_and_not_the_movie(tmp_path):
    ops, im = build(tmp_path, np.broadcast_to(FLAT[:, None, None], (FRAMES, LY, LX)))
    ops["reg_file"] = str(tmp_path / "missing.bin")
    data = EditRoiData(images=None, im=im)
    data.temp_delete_roi[0] = None
    iscell = np.array([CellType.TEMP_DELETE, CellType.ROI])

    info = commit_edit(data, Suite2pData(ops), iscell, str(tmp_path), "suite2p_roi_t")

    assert list(info["iscell"].data) == [CellType.NON_ROI, CellType.ROI]
    assert info["ops"].data["F"].shape == (2, FRAMES)
    assert data.delete_roi == [0]


def test_delete_all_then_add_keeps_rows_aligned(tmp_path):
    movie = np.broadcast_to(FLAT[:, None, None], (FRAMES, LY, LX)).copy()
    roi_y, roi_x = disk(NEW_ROI.posy, NEW_ROI.posx, 3)
    movie[:, roi_y, roi_x] += 500
    ops, im = build(tmp_path, movie)

    # every ROI deleted, then committed
    data = EditRoiData(images=None, im=im)
    for i in range(len(CELLS)):
        data.temp_delete_roi[i] = None
    iscell = np.array([CellType.TEMP_DELETE] * len(CELLS))

    info = commit_edit(data, Suite2pData(ops), iscell, str(tmp_path), "suite2p_roi_t")

    ops = info["ops"].data
    assert list(info["iscell"].data) == [CellType.NON_ROI] * len(CELLS)
    assert ops["F"].shape[0] == len(ops["stat"]) == len(info["edit_roi_data"].im)

    # an ROI added afterwards must land on the same row in im and in F
    data = info["edit_roi_data"]
    data.im = np.vstack((data.im, create_ellipse_mask((LY, LX), NEW_ROI)[None]))
    new_id = len(CELLS)
    data.temp_add_roi[new_id] = NEW_ROI
    iscell = np.append(info["iscell"].data, CellType.TEMP_ADD)

    info = commit_edit(data, Suite2pData(ops), iscell, str(tmp_path), "suite2p_roi_t")

    ops = info["ops"].data
    assert ops["F"].shape[0] == ops["Fneu"].shape[0] == new_id + 1
    assert len(ops["stat"]) == new_id + 1
    assert len(info["fluorescence"].data) == len(info["edit_roi_data"].im)
    assert list(info["iscell"].data) == [CellType.NON_ROI] * new_id + [CellType.ROI]
    assert np.allclose(ops["F"][new_id], FLAT + 500, atol=1e-2)
