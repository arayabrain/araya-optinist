import copy
import io
import pickle
import warnings
from types import SimpleNamespace

import numpy as np
import pytest

from studio.app.optinist.core.edit_ROI.utils import ellipse_pixels
from studio.app.optinist.dataclass import EditRoiData
from studio.app.optinist.dataclass.roi import RoiMasks
from studio.app.optinist.schemas.roi import RoiPos

LY, LX = 7, 5


def random_stack(n=12, seed=1):
    """A per-ROI stack as the producers build it: i on ROI i's pixels, NaN
    elsewhere; overlapping ROIs, and an empty one."""
    rng = np.random.default_rng(seed)
    stack = np.full((n, LY, LX), np.nan)
    for i in range(n):
        if i == 3:
            continue  # CaImAn's get_roi can return an empty footprint
        y0, x0 = rng.integers(0, LY - 2), rng.integers(0, LX - 2)
        stack[i, y0 : y0 + rng.integers(1, 4), x0 : x0 + rng.integers(1, 4)] = i
    return stack


def nanmax(stack, rows=slice(None)):
    return np.nanmax(stack[rows], axis=0, initial=np.nan)


@pytest.mark.parametrize(
    "rows", [None, [0, 2, 5], np.arange(12) % 3 == 0, np.zeros(12, bool), [9, 0]]
)
def test_label_image_is_nanmax_over_the_stack(rows):
    stack = random_stack()
    masks = RoiMasks.from_stack(stack)
    want = nanmax(stack, slice(None) if rows is None else rows)
    np.testing.assert_array_equal(masks.label_image(rows), want)


@pytest.mark.parametrize("rows", [[1, 0], [0, 1], np.array([True, True])])
def test_the_highest_roi_wins_where_rois_overlap(rows):
    stack = np.full((2, LY, LX), np.nan)
    stack[0, 1:4, 1:4] = 0
    stack[1, 3:5, 3:5] = 1  # covers (3, 3) of ROI 0
    image = RoiMasks.from_stack(stack).label_image(rows)
    assert image[3, 3] == 1
    np.testing.assert_array_equal(image, nanmax(stack))


def test_pixels_select_what_the_boolean_mask_selected():
    stack = random_stack()
    masks = RoiMasks.from_stack(stack)
    movie = np.arange(4 * LY * LX, dtype=np.float32).reshape(4, LY, LX) ** 1.5
    assert len(masks) == len(stack) and masks.shape == (LY, LX)
    for i in range(len(stack)):
        rows, cols = masks.coords(i)
        np.testing.assert_array_equal(
            movie[:, rows, cols], movie[:, ~np.isnan(stack[i])]
        )


def test_append_union_and_select_match_the_stack_operations():
    stack = random_stack()
    masks = RoiMasks.from_stack(stack)

    # merge, as EditROI.merge did: the union of the rows, valued as the new row
    merged = np.where(np.isnan(stack[[2, 4, 6]]).all(axis=0), np.nan, len(stack))
    stack = np.vstack((stack, merged[None]))
    masks.append(masks.union([2, 4, 6]))
    assert len(masks) == len(stack)
    np.testing.assert_array_equal(masks.label_image(), nanmax(stack))

    # drop rows, as EditROI's pending-row cleanup did: keep, then renumber
    keep = np.ones(len(stack), bool)
    keep[[1, 5]] = False
    kept = stack[keep]
    for new in range(len(kept)):
        kept[new][~np.isnan(kept[new])] = new
    np.testing.assert_array_equal(masks.select(keep).label_image(), nanmax(kept))
    np.testing.assert_array_equal(
        masks.select(slice(4)).label_image(), nanmax(stack[:4])
    )


def test_an_empty_set_keeps_its_frame():
    masks = RoiMasks.from_stack(np.zeros((0, LY, LX)))
    assert len(masks) == 0 and masks.shape == (LY, LX)
    assert np.isnan(masks.label_image()).all()
    assert masks.label_image().shape == (LY, LX)


@pytest.mark.parametrize("shape", [(8, 8), (6, 9), (9, 6)])
def test_ellipse_pixels(shape):
    pos = RoiPos(posx=5, posy=2, sizex=4, sizey=2)
    rows, cols = np.unravel_index(ellipse_pixels(shape, pos), shape)
    # posx is the column, posy the row
    assert set(zip(rows.tolist(), cols.tolist())) >= {(2, 4), (2, 5), (1, 5), (3, 5)}
    assert (abs(rows - 2) <= 1).all() and (abs(cols - 5) <= 2).all()


def test_ellipse_pixels_match_the_dense_mask_on_square_frames():
    # the dense mask EditROI drew before, which only worked on square frames
    shape = (8, 8)
    pos = RoiPos(posx=4, posy=3, sizex=5, sizey=3)
    xx, yy = np.meshgrid(np.arange(shape[0]), np.arange(shape[1]))
    distance = ((xx - 4) / 2.5) ** 2 + ((yy - 3) / 1.5) ** 2
    np.testing.assert_array_equal(
        ellipse_pixels(shape, pos), np.flatnonzero(distance <= 1)
    )


def full_frame_ellipse(shape, x, y, width, height):
    """The ellipse over the whole frame, as EditROI computed it before."""
    yy, xx = np.meshgrid(np.arange(shape[0]), np.arange(shape[1]), indexing="ij")
    distance = ((xx - x) / (width / 2)) ** 2 + ((yy - y) / (height / 2)) ** 2
    return np.flatnonzero(distance <= 1)


@pytest.mark.parametrize("shape", [(7, 5), (6, 9)])
def test_ellipse_pixels_equal_the_full_frame_computation(shape):
    rng = np.random.default_rng(3)
    for _ in range(300):  # inside, across the edges and outside the frame
        x, y = (int(v) for v in rng.integers(-5, 13, 2))
        width, height = (int(v) for v in rng.integers(1, 9, 2))
        pos = RoiPos(posx=x, posy=y, sizex=width, sizey=height)
        np.testing.assert_array_equal(
            ellipse_pixels(shape, pos), full_frame_ellipse(shape, x, y, width, height)
        )


def test_an_ellipse_below_one_pixel_has_no_pixels():
    # RoiPos refuses sizes below 1; a caller passing its own object can not
    pos = SimpleNamespace(posx=2, posy=2, sizex=0.4, sizey=3)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # no divide-by-zero warning either
        assert ellipse_pixels((LY, LX), pos).size == 0


def pickled_globals(blob):
    """The (module, name) pairs a pickle stream needs to load."""
    names = set()

    class Recorder(pickle.Unpickler):
        def find_class(self, module, name):
            names.add((module, name))
            return super().find_class(module, name)

    Recorder(io.BytesIO(blob)).load()
    return names


def test_the_pickle_names_no_class_an_earlier_release_lacks():
    data = EditRoiData(images=None, roi_masks=RoiMasks.from_stack(random_stack()))
    blob = pickle.dumps(data)
    allowed_modules = {"builtins", "copyreg", "_codecs"}
    for module, name in pickled_globals(blob):
        assert (
            module.startswith("numpy")
            or module in allowed_modules
            or (module, name) == ("studio.app.optinist.dataclass.roi", "EditRoiData")
        ), (module, name)
    state = data.__getstate__()
    assert "roi_masks" in state and "im" not in state


def test_edit_roi_data_takes_roi_masks_only():
    with pytest.raises(TypeError, match="not RoiMasks"):
        EditRoiData(images=None, roi_masks=random_stack())


def test_pickling_round_trips_the_masks_and_the_edit_state():
    stack = random_stack()
    data = EditRoiData(images=None, roi_masks=RoiMasks.from_stack(stack))
    data.temp_add_roi = {12: RoiPos(posx=1, posy=1, sizex=2, sizey=2)}
    back = pickle.loads(pickle.dumps(data))
    np.testing.assert_array_equal(back.roi_masks.label_image(), nanmax(stack))
    assert back.temp_add_roi == data.temp_add_roi
    copied = copy.deepcopy(data)  # WorkflowFilter.filter_data
    np.testing.assert_array_equal(copied.roi_masks.label_image(), nanmax(stack))


def legacy_pickle(monkeypatch, im):
    """A node pickle's EditRoiData as releases before RoiMasks wrote it."""
    legacy = EditRoiData(
        images=None, roi_masks=RoiMasks.from_stack(np.zeros((0, LY, LX)))
    )
    state = {k: v for k, v in vars(legacy).items() if not k.startswith("_")}
    state["im"] = im
    with monkeypatch.context() as m:
        m.setattr(EditRoiData, "__getstate__", lambda self: state)
        return pickle.dumps(legacy)


def test_a_legacy_stack_converts_when_first_needed(monkeypatch):
    stack = random_stack()
    back = pickle.loads(legacy_pickle(monkeypatch, stack))
    assert back._roi_masks is None  # loading alone converts nothing
    # written back unchanged while nothing needed the masks
    again = pickle.loads(pickle.dumps(back))
    assert again._roi_masks is None
    np.testing.assert_array_equal(again._stack, stack)
    np.testing.assert_array_equal(back.roi_masks.label_image(), nanmax(stack))
    # once converted, the masks are what is written
    assert pickle.loads(pickle.dumps(back))._stack is None
