import numpy as np
import pytest

from studio.app.common.dataclass import ImageData
from studio.app.optinist.dataclass import FluoData, IscellData
from studio.app.optinist.wrappers.data_utils.roi_fluo_from_hdf5 import (
    roi_fluo_from_hdf5,
)
from studio.app.optinist.wrappers.data_utils.roi_from_hdf5 import roi_from_hdf5

H = W = 6

LOADERS = {
    "roi_from_hdf5": lambda img, out, iscell: roi_from_hdf5(img, out, iscell=iscell),
    "roi_fluo_from_hdf5": lambda img, out, iscell: roi_fluo_from_hdf5(
        img, FluoData(np.ones((2, 5))), out, iscell=iscell, params={"transpose": False}
    ),
}


@pytest.mark.parametrize("loader", LOADERS)
@pytest.mark.parametrize("dtype", [np.float32, np.uint8])
@pytest.mark.parametrize("label", [True, False], ids=["all_cells", "no_cells"])
def test_an_empty_cell_or_non_cell_set_gives_an_all_nan_image(
    tmp_path, loader, dtype, label
):
    stack = np.zeros((2, H, W), dtype)
    stack[0, 1, 1], stack[1, 4, 4] = 1, 2
    cell_img = ImageData(stack, output_dir=str(tmp_path), file_name="cell_img")

    info = LOADERS[loader](cell_img, str(tmp_path), IscellData(np.full(2, label)))

    filled, empty = (
        ("cell_roi", "non_cell_roi") if label else ("non_cell_roi", "cell_roi")
    )
    assert np.isnan(info[empty].data).all()
    np.testing.assert_array_equal(info[filled].data, stack.max(axis=0))


def label_case():
    """Three ROIs and their label image; ROI 2 (non-cell) covers part of ROI 0."""
    stack = np.full((3, H, W), np.nan, np.float32)
    stack[0, 1:3, 1:3] = 0
    stack[1, 4, 4] = 1
    stack[2, 2, 2:4] = 2
    iscell = np.array([1, 1, 0])
    return stack, np.nanmax(stack, axis=0), iscell


@pytest.mark.parametrize("loader", LOADERS)
@pytest.mark.parametrize("layers", [1, 0], ids=["one_layer", "2d"])
def test_a_label_image_gives_the_same_images_away_from_overlaps(
    tmp_path, loader, layers
):
    stack, label, iscell = label_case()
    written = label[np.newaxis] if layers else label  # NWB writes one layer
    cell_img = ImageData(written, output_dir=str(tmp_path), file_name="cell_img")

    info = LOADERS[loader](cell_img, str(tmp_path), IscellData(iscell))

    np.testing.assert_array_equal(info["all_roi"].data, label)
    np.testing.assert_array_equal(
        info["non_cell_roi"].data, np.nanmax(stack[iscell == 0], axis=0)
    )
    # ROI 0's pixel under ROI 2 shows ROI 2 in the label image, so it is not a
    # cell pixel there; everywhere else the cell image is the stack's
    from_stack = np.nanmax(stack[iscell != 0], axis=0)
    from_stack[2, 2] = np.nan
    np.testing.assert_array_equal(info["cell_roi"].data, from_stack)


@pytest.mark.parametrize("loader", LOADERS)
def test_a_per_roi_stack_from_an_older_file_still_reads(tmp_path, loader):
    stack, label, iscell = label_case()
    cell_img = ImageData(stack, output_dir=str(tmp_path), file_name="cell_img")

    info = LOADERS[loader](cell_img, str(tmp_path), IscellData(iscell))

    np.testing.assert_array_equal(info["all_roi"].data, label)
    np.testing.assert_array_equal(
        info["cell_roi"].data, np.nanmax(stack[iscell != 0], axis=0)
    )


@pytest.mark.parametrize("loader", LOADERS)
@pytest.mark.parametrize(
    "case, n_iscell, message",
    [
        ("stack", 2, "3 ROI layers, so iscell needs 3 entries; its length is 2"),
        ("label", 2, "up to 2, so iscell needs at least 3 entries; its length is 2"),
    ],
)
def test_an_iscell_that_does_not_fit_cell_img_is_refused_by_name(
    tmp_path, loader, case, n_iscell, message
):
    # before: a reshape error on the stack, and a label image read as if iscell
    # covered it
    stack, label, _ = label_case()
    written = stack if case == "stack" else label[np.newaxis]
    cell_img = ImageData(written, output_dir=str(tmp_path), file_name="cell_img")

    with pytest.raises(ValueError, match=message):
        LOADERS[loader](cell_img, str(tmp_path), IscellData(np.ones(n_iscell, int)))


@pytest.mark.parametrize("loader", LOADERS)
def test_one_layer_beside_a_one_entry_iscell_reads_as_a_one_roi_stack(tmp_path, loader):
    # a binary mask from another tool: its ROI holds 1, not the label 0; one
    # layer and one iscell entry read as one ROI, as earlier releases read it
    mask = np.full((1, H, W), np.nan, np.float32)
    mask[0, 1:3, 1:3] = 1
    cell_img = ImageData(mask, output_dir=str(tmp_path), file_name="cell_img")

    info = LOADERS[loader](cell_img, str(tmp_path), IscellData(np.array([1])))

    np.testing.assert_array_equal(info["cell_roi"].data, mask[0])
    assert np.isnan(info["non_cell_roi"].data).all()


@pytest.mark.parametrize("loader", LOADERS)
def test_a_label_image_may_leave_covered_rois_without_a_pixel(tmp_path, loader):
    # ROI 3 lies under others: no label 3 in the image, but iscell has it
    _, label, iscell = label_case()
    cell_img = ImageData(label[np.newaxis], output_dir=str(tmp_path), file_name="c")

    info = LOADERS[loader](cell_img, str(tmp_path), IscellData(np.append(iscell, 1)))

    np.testing.assert_array_equal(info["all_roi"].data, label)
