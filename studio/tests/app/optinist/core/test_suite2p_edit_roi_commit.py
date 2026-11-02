import asyncio
import shutil
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
from studio.app.common.core.experiment.experiment_writer import (
    ExptConfigWriter,
    ExptDataWriter,
)
from studio.app.common.core.storage.remote_storage_controller import RemoteStorageType
from studio.app.common.core.utils.pickle_handler import PickleReader, PickleWriter
from studio.app.dir_path import DIRPATH
from studio.app.optinist.core.edit_ROI.edit_ROI import CellType
from studio.app.optinist.core.edit_ROI.utils import ellipse_pixels
from studio.app.optinist.core.edit_ROI.wrappers.suite2p_edit_roi import commit_edit
from studio.app.optinist.dataclass import EditRoiData, Suite2pData
from studio.app.optinist.dataclass.roi import RoiMasks
from studio.app.optinist.schemas.roi import RoiPos

LY = LX = 40
FRAMES = 6
# frame t is flat at 100 + 10 t, so any mean over any pixel set is 100 + 10 t
FLAT = (np.arange(FRAMES) * 10 + 100).astype(np.int16)
CELLS = [(8, 8), (30, 22)]  # the second one sits inside the new ROI's neuropil box
NEW_ROI = RoiPos(posx=30, posy=30, sizex=6, sizey=6)
# mean row of the new ROI's neuropil ring under suite2p's geometry: the ring's
# bounding box is clipped by the bottom FOV edge, so it is not the ROI's 30
RING_MEAN_Y = 88 / 3


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
        "nframes": FRAMES,
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


def with_ellipse(im, roi_pos):
    """The ROIs of im and, after them, the ellipse EditROI.add draws."""
    roi_masks = RoiMasks.from_stack(im)
    roi_masks.append(ellipse_pixels((LY, LX), roi_pos))
    return roi_masks


def test_add_extracts_trace_and_neuropil_from_the_registered_movie(tmp_path):
    movie = np.broadcast_to(FLAT[:, None, None], (FRAMES, LY, LX)).copy()
    roi_y, roi_x = disk(NEW_ROI.posy, NEW_ROI.posx, 3)
    movie[:, roi_y, roi_x] += 500
    # a bright neighbouring cell must not leak into the new ROI's neuropil
    cell_y, cell_x = disk(*CELLS[1], 3)
    movie[:, cell_y, cell_x] += 5000
    ops, im = build(tmp_path, movie)
    data = EditRoiData(images=None, roi_masks=with_ellipse(im, NEW_ROI))
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
    data = EditRoiData(
        images=None,
        roi_masks=RoiMasks.from_stack(np.vstack((im, np.nanmax(im, axis=0)[None]))),
    )
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
    data = EditRoiData(images=None, roi_masks=RoiMasks.from_stack(im))
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
    data = EditRoiData(images=None, roi_masks=RoiMasks.from_stack(im))
    for i in range(len(CELLS)):
        data.temp_delete_roi[i] = None
    iscell = np.array([CellType.TEMP_DELETE] * len(CELLS))

    info = commit_edit(data, Suite2pData(ops), iscell, str(tmp_path), "suite2p_roi_t")

    ops = info["ops"].data
    assert list(info["iscell"].data) == [CellType.NON_ROI] * len(CELLS)
    assert ops["F"].shape[0] == len(ops["stat"]) == len(info["edit_roi_data"].roi_masks)

    # an ROI added afterwards must land on the same row in the masks and in F
    data = info["edit_roi_data"]
    data.roi_masks.append(ellipse_pixels((LY, LX), NEW_ROI))
    new_id = len(CELLS)
    data.temp_add_roi[new_id] = NEW_ROI
    iscell = np.append(info["iscell"].data, CellType.TEMP_ADD)

    info = commit_edit(data, Suite2pData(ops), iscell, str(tmp_path), "suite2p_roi_t")

    ops = info["ops"].data
    assert ops["F"].shape[0] == ops["Fneu"].shape[0] == new_id + 1
    assert len(ops["stat"]) == new_id + 1
    assert len(info["fluorescence"].data) == len(info["edit_roi_data"].roi_masks)
    assert list(info["iscell"].data) == [CellType.NON_ROI] * new_id + [CellType.ROI]
    assert np.allclose(ops["F"][new_id], FLAT + 500, atol=1e-2)


def test_a_frame_count_disagreeing_with_the_stored_traces_is_named(tmp_path):
    ops, im = build(tmp_path, np.broadcast_to(FLAT[:, None, None], (FRAMES, LY, LX)))
    ops["nframes"] = FRAMES - 1
    data = EditRoiData(images=None, roi_masks=with_ellipse(im, NEW_ROI))
    data.temp_add_roi[2] = NEW_ROI
    iscell = np.array([CellType.ROI, CellType.ROI, CellType.TEMP_ADD])

    with pytest.raises(ValueError, match=f"nframes.* {FRAMES - 1} .* {FRAMES} frames"):
        commit_edit(data, Suite2pData(ops), iscell, str(tmp_path), "suite2p_roi_t")


@pytest.mark.parametrize(
    "roi_pos",
    [
        RoiPos(posx=10, posy=20, sizex=3, sizey=3),
        RoiPos(posx=30, posy=31, sizex=7, sizey=5),
        RoiPos(posx=20, posy=30, sizex=5, sizey=9),
    ],
)
def test_an_added_roi_keeps_the_pixels_the_editor_drew(tmp_path, roi_pos):
    # odd sizes: suite2p's own ellipse for an added cell differs from the
    # editor's; the trace, the ROI table and the label image follow the editor
    drawn = ellipse_pixels((LY, LX), roi_pos)
    movie = np.broadcast_to(FLAT[:, None, None], (FRAMES, LY, LX)).copy()
    movie.reshape(FRAMES, -1)[:, drawn] += 500
    ops, im = build(tmp_path, movie)
    data = EditRoiData(images=None, roi_masks=with_ellipse(im, roi_pos))
    data.temp_add_roi[2] = roi_pos
    iscell = np.array([CellType.ROI, CellType.ROI, CellType.TEMP_ADD])

    info = commit_edit(data, Suite2pData(ops), iscell, str(tmp_path), "suite2p_roi_t")

    new = info["ops"].data["stat"][2]
    pixels = np.ravel_multi_index((new["ypix"], new["xpix"]), (LY, LX))
    np.testing.assert_array_equal(np.sort(pixels), np.sort(drawn))
    assert np.allclose(info["ops"].data["F"][2], FLAT + 500, atol=1e-2)
    (entry,) = info["nwbfile"]["ROI"].values()
    rows, cols, _ = entry["roi_list"][2]["pixel_mask"].T
    table = np.ravel_multi_index((rows.astype(int), cols.astype(int)), (LY, LX))
    np.testing.assert_array_equal(np.sort(table), np.sort(drawn))


def test_a_one_pixel_roi_commits_its_pixel(tmp_path):
    ops, im = build(tmp_path, np.broadcast_to(FLAT[:, None, None], (FRAMES, LY, LX)))
    tiny = RoiPos(posx=30, posy=30, sizex=1, sizey=1)
    data = EditRoiData(images=None, roi_masks=with_ellipse(im, tiny))
    data.temp_add_roi[2] = tiny
    iscell = np.array([CellType.ROI, CellType.ROI, CellType.TEMP_ADD])

    info = commit_edit(data, Suite2pData(ops), iscell, str(tmp_path), "suite2p_roi_t")

    new = info["ops"].data["stat"][2]
    assert (list(new["ypix"]), list(new["xpix"])) == ([30], [30])
    assert np.allclose(info["ops"].data["F"][2], FLAT, atol=1e-2)


@pytest.mark.parametrize("size", [1, 3])
def test_an_roi_drawn_entirely_on_a_cell_takes_its_trace_from_all_its_pixels(
    tmp_path, size
):
    # without allow_overlap, overlapping pixels leave the trace; with none of
    # its own left, an added ROI would commit as a cell with an all-zero trace
    movie = np.broadcast_to(FLAT[:, None, None], (FRAMES, LY, LX)).copy()
    cell_y, cell_x = disk(*CELLS[0], 3)
    movie[:, cell_y, cell_x] += 500
    ops, im = build(tmp_path, movie)
    on_cell = RoiPos(posx=CELLS[0][1], posy=CELLS[0][0], sizex=size, sizey=size)
    data = EditRoiData(images=None, roi_masks=with_ellipse(im, on_cell))
    data.temp_add_roi[2] = on_cell
    iscell = np.array([CellType.ROI, CellType.ROI, CellType.TEMP_ADD])

    info = commit_edit(data, Suite2pData(ops), iscell, str(tmp_path), "suite2p_roi_t")

    new = info["ops"].data["stat"][2]
    assert new["overlap"].all()
    assert np.allclose(info["ops"].data["F"][2], FLAT + 500, atol=1e-2)


def test_a_partly_overlapping_roi_still_leaves_out_the_shared_pixels(tmp_path):
    movie = np.broadcast_to(FLAT[:, None, None], (FRAMES, LY, LX)).copy()
    cell_y, cell_x = disk(*CELLS[0], 3)
    movie[:, cell_y, cell_x] += 500
    ops, im = build(tmp_path, movie)
    around = RoiPos(posx=CELLS[0][1], posy=CELLS[0][0], sizex=9, sizey=9)
    data = EditRoiData(images=None, roi_masks=with_ellipse(im, around))
    data.temp_add_roi[2] = around
    iscell = np.array([CellType.ROI, CellType.ROI, CellType.TEMP_ADD])

    info = commit_edit(data, Suite2pData(ops), iscell, str(tmp_path), "suite2p_roi_t")

    new = info["ops"].data["stat"][2]
    assert new["overlap"].any() and not new["overlap"].all()
    assert np.allclose(info["ops"].data["F"][2], FLAT, atol=1e-2)


def test_an_roi_with_no_pixel_is_refused_by_name(tmp_path):
    ops, im = build(tmp_path, np.broadcast_to(FLAT[:, None, None], (FRAMES, LY, LX)))
    # the stored pixels decide, whatever the RoiPos says
    roi_masks = RoiMasks.from_stack(im)
    roi_masks.append(np.zeros(0, np.int64))
    data = EditRoiData(images=None, roi_masks=roi_masks)
    data.temp_add_roi[2] = RoiPos(posx=30, posy=30, sizex=1, sizey=1)
    iscell = np.array([CellType.ROI, CellType.ROI, CellType.TEMP_ADD])

    with pytest.raises(ValueError, match="covers no pixel"):
        commit_edit(data, Suite2pData(ops), iscell, str(tmp_path), "suite2p_roi_t")


def test_neuropil_follows_the_ring_geometry_on_a_ramped_background(tmp_path):
    # a ramp along y makes every mean depend on exactly which pixels it covers
    movie = (
        np.broadcast_to(FLAT[:, None, None], (FRAMES, LY, LX)) + np.arange(LY)[:, None]
    )
    ops, im = build(tmp_path, movie)
    data = EditRoiData(images=None, roi_masks=with_ellipse(im, NEW_ROI))
    data.temp_add_roi[2] = NEW_ROI
    iscell = np.array([CellType.ROI, CellType.ROI, CellType.TEMP_ADD])

    info = commit_edit(data, Suite2pData(ops), iscell, str(tmp_path), "suite2p_roi_t")

    ops = info["ops"].data
    # the disk is symmetric about y = 30, so its mean is the ramp at its centre
    assert np.allclose(ops["F"][2], FLAT + 30, atol=1e-2)
    assert np.allclose(ops["Fneu"][2], FLAT + RING_MEAN_Y, atol=1e-2)


def test_extraction_settings_are_read_from_a_1_1_0_pickle(tmp_path):
    # suite2p 1.1.0 pickles nest them under settings.extraction and carry no
    # flat copies; the same ring geometry must come out
    movie = (
        np.broadcast_to(FLAT[:, None, None], (FRAMES, LY, LX)) + np.arange(LY)[:, None]
    )
    ops, im = build(tmp_path, movie)
    ops["settings"] = {
        "extraction": {
            key: ops.pop(key)
            for key in ("allow_overlap", "inner_neuropil_radius", "min_neuropil_pixels")
        }
    }
    data = EditRoiData(images=None, roi_masks=with_ellipse(im, NEW_ROI))
    data.temp_add_roi[2] = NEW_ROI
    iscell = np.array([CellType.ROI, CellType.ROI, CellType.TEMP_ADD])

    info = commit_edit(data, Suite2pData(ops), iscell, str(tmp_path), "suite2p_roi_t")

    ops = info["ops"].data
    assert np.allclose(ops["F"][2], FLAT + 30, atol=1e-2)
    assert np.allclose(ops["Fneu"][2], FLAT + RING_MEAN_Y, atol=1e-2)


def test_commit_never_reads_the_movie_edit_roi_data_carries(tmp_path):
    # suite2p_roi stores a reference to the input movie in EditRoiData; editing
    # reads traces from the registered binary, so the reference is never opened
    from studio.app.common.dataclass.image import ImageData

    movie = np.broadcast_to(FLAT[:, None, None], (FRAMES, LY, LX)).copy()
    ops, im = build(tmp_path, movie)
    unread = ImageData(str(tmp_path / "missing.tif"))  # opening it would raise
    data = EditRoiData(images=unread, roi_masks=with_ellipse(im, NEW_ROI))
    data.temp_add_roi[2] = NEW_ROI
    iscell = np.array([CellType.ROI, CellType.ROI, CellType.TEMP_ADD])

    info = commit_edit(data, Suite2pData(ops), iscell, str(tmp_path), "suite2p_roi_x")

    assert len(info["fluorescence"].data) == 3
    assert info["edit_roi_data"].images is unread


SOURCE_ID, COPY_ID = "cccc3333", "dddd4444"


@pytest.fixture
def experiments(monkeypatch):
    """A source experiment's directory and its copy's, removed afterwards."""
    monkeypatch.setenv("REMOTE_STORAGE_TYPE", RemoteStorageType.NO_USE.value)
    dirs = [Path(DIRPATH.OUTPUT_DIR, "default", uid) for uid in (SOURCE_ID, COPY_ID)]
    for path in dirs:
        shutil.rmtree(path, ignore_errors=True)
    yield dirs
    for path in dirs:
        shutil.rmtree(path, ignore_errors=True)


def test_a_commit_in_a_copied_experiment_reads_only_the_copy(experiments):
    """Experiment copy must rewrite reg_file through suite2p 1.1.0's ops, whose
    Vmap is an object array of maps of different sizes."""
    source, copied = experiments
    movie = np.broadcast_to(FLAT[:, None, None], (FRAMES, LY, LX)).copy()
    roi_y, roi_x = disk(NEW_ROI.posy, NEW_ROI.posx, 3)
    movie[:, roi_y, roi_x] += 500
    (source / "suite2p_roi_x").mkdir(parents=True)
    ops, im = build(source / "suite2p_roi_x", movie)
    ops["Vmap"] = np.empty(2, dtype=object)
    ops["Vmap"][0], ops["Vmap"][1] = np.zeros((4, 4)), np.zeros((2, 2))
    PickleWriter.write(
        str(source / "suite2p_roi_x" / "suite2p_roi.pkl"),
        {
            "ops": Suite2pData(ops),
            "edit_roi_data": EditRoiData(
                images=None, roi_masks=RoiMasks.from_stack(im)
            ),
        },
    )
    config = asdict(ExptConfigReader.create_empty_experiment_config())
    ExptConfigWriter._write_raw("default", SOURCE_ID, config)

    writer = ExptDataWriter("bucket", "default", SOURCE_ID)
    assert asyncio.run(writer.copy_data(COPY_ID, "copy"))
    shutil.rmtree(source)  # the copy must not depend on the source

    output = PickleReader.read(str(copied / "suite2p_roi_x" / "suite2p_roi.pkl"))
    data = output["edit_roi_data"]
    data.roi_masks.append(ellipse_pixels((LY, LX), NEW_ROI))
    data.temp_add_roi[2] = NEW_ROI
    iscell = np.array([CellType.ROI, CellType.ROI, CellType.TEMP_ADD])

    info = commit_edit(
        data, output["ops"], iscell, str(copied / "suite2p_roi_x"), "suite2p_roi_x"
    )

    assert np.allclose(info["ops"].data["F"][2], FLAT + 500, atol=1e-2)


def test_suite2p_roi_stores_a_reference_to_the_movie(tmp_path, monkeypatch):
    # runs the wrapper with a stubbed suite2p, so reading the movie back into
    # EditRoiData (ImageData(...).data) fails here
    import sys
    import types

    import tifffile

    from studio.app.common.dataclass.image import ImageData
    from studio.app.optinist.wrappers.suite2p.roi import suite2p_roi
    from studio.tests.app.optinist.suite2p_settings_1_1_0 import default_settings

    movie = tmp_path / "movie.tif"
    tifffile.imwrite(movie, np.zeros((FRAMES, LY, LX), np.int16))
    ops, im = build(tmp_path, np.zeros((FRAMES, LY, LX)))
    stat = ops.pop("stat")

    class BinaryFile:
        def __init__(self, Ly, Lx, filename, n_frames=None, write=False):
            self.shape = (n_frames, Ly, Lx)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def pipeline(save_path, f_reg, run_registration, settings, device):
        detect_outputs = {
            "max_proj": np.zeros((LY, LX)),
            "Vcorr": np.zeros((LY, LX)),
            "meanImg_crop": np.zeros((LY, LX)),
            "diameter": np.array([12.0, 12.0]),
        }
        iscell = np.array([[1, 0.9], [0, 0.1]])
        return (
            None,
            detect_outputs,
            np.array(stat, dtype=object),
            ops["F"],
            ops["Fneu"],
            None,
            None,
            None,
            iscell,
            None,
            None,
            {},
        )

    fake = types.ModuleType("suite2p")
    fake.default_settings = default_settings
    fake.io = types.SimpleNamespace(BinaryFile=BinaryFile)
    fake.pipeline = pipeline
    run_s2p = types.ModuleType("suite2p.run_s2p")
    run_s2p._assign_torch_device = lambda name: name
    monkeypatch.setitem(sys.modules, "suite2p", fake)
    monkeypatch.setitem(sys.modules, "suite2p.run_s2p", run_s2p)

    def no_full_read(self):
        # pytest.fail, not an AssertionError: the wrapper's except Exception
        # around detection would swallow that
        pytest.fail("suite2p_roi read the movie")

    monkeypatch.setattr(ImageData, "data", property(no_full_read))

    output_dir = tmp_path / "output" / "default" / "uid" / "suite2p_roi_x"
    output_dir.mkdir(parents=True)
    ops["file_list"] = [str(movie)]
    info = suite2p_roi(
        Suite2pData(ops),
        str(output_dir),
        params={},
        # a 1 Hz rate keeps the detection bin within the 6-frame movie
        nwbfile={"imaging_plane": {"imaging_rate": 1.0}},
    )

    assert len(info["fluorescence"].data) == len(CELLS)  # detection really ran
    images = info["edit_roi_data"].images
    assert isinstance(images, ImageData)
    assert images.path == [str(movie)]
