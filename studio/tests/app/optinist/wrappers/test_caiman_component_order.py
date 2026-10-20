import sys
import types

import numpy as np
import pytest
import scipy.sparse as sp

from studio.app.optinist.wrappers.caiman import cnmf

# caiman used to build im in accepted-then-rejected order while fluorescence kept
# the raw component order, so im[i] and F[i] described different components as
# soon as the accepted set was not a prefix. Every row-indexed consumer, the ROI
# editor included, then read the wrong trace.
DIMS = (4, 4)
FRAMES = 5
PIXELS = DIMS[0] * DIMS[1]


def labelled_masks(A, roi_thr, thr_method, swap_dim, dims):
    # get_roi labels the pixels of the k-th passed component with k + 1, reading
    # caiman's pixel index in Fortran order
    dense = A.toarray()
    order = "C" if swap_dim else "F"
    return [
        (dense[:, k].reshape(dims, order=order) > 0) * (k + 1.0)
        for k in range(dense.shape[1])
    ]


def test_every_output_row_is_the_same_component(monkeypatch):
    monkeypatch.setattr(cnmf, "get_roi", labelled_masks)
    # component c occupies image row c; trace c is 10 c + t
    A = sp.csc_matrix(
        np.array(
            [
                [1.0 if p % DIMS[0] == c else 0.0 for c in range(3)]
                for p in range(PIXELS)
            ]
        )
    )
    C = np.arange(3)[:, None] * 10.0 + np.arange(FRAMES)
    idx_good, idx_bad = [2, 0], [1]  # deliberately not a prefix

    (
        im,
        iscell,
        roi_list,
        F,
        non_cell_roi,
        n_rois,
        n_noncell,
        order,
    ) = cnmf.component_outputs(
        A,
        C,
        idx_good,
        idx_bad,
        DIMS,
        0.9,
        "nrg",
        False,
        accepted_list=[2, 0],
        rejected_list=[1],
        n_frames=FRAMES,
    )

    assert len(F) == len(im) == len(roi_list) == 3
    assert list(iscell) == [1, 1, 0]
    for row, comp in enumerate(idx_good + idx_bad):
        assert np.array_equal(F[row], C[comp])
        assert set(np.argwhere(~np.isnan(im[row]))[:, 0]) == {comp}
        assert np.nanmax(im[row]) == row
        expected_mask = A.toarray()[:, comp].reshape(DIMS, order="F")
        assert np.array_equal(roi_list[row]["image_mask"], expected_mask)
    assert roi_list[0]["accepted"] and roi_list[2]["rejected"]
    assert (n_rois, n_noncell) == (2, 1)
    assert np.nanmax(non_cell_roi) == 2
    assert order == idx_good + idx_bad


def test_dff_rows_are_the_fluorescence_rows(monkeypatch):
    # caiman_cnmf orders dff with the order component_outputs returns
    monkeypatch.setattr(cnmf, "get_roi", labelled_masks)
    utilities = types.ModuleType("caiman.source_extraction.cnmf.utilities")
    utilities.detrend_df_f = lambda A, b, C, f, **kwargs: C * 2.0
    for name in ("caiman", "caiman.source_extraction", "caiman.source_extraction.cnmf"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, utilities.__name__, utilities)
    A = sp.csc_matrix(np.eye(PIXELS)[:, :3])
    C = np.arange(3)[:, None] * 10.0 + np.arange(FRAMES)
    estimates = types.SimpleNamespace(
        A=A, b=np.ones((PIXELS, 1)), C=C, f=np.ones((1, FRAMES)), YrA=None
    )

    *_, F, _, _, _, order = cnmf.component_outputs(
        A, C, [2, 0], [1], DIMS, 0.9, "nrg", False, n_frames=FRAMES
    )
    dff = cnmf.caiman_dff(estimates, order, F.shape[1], 500)

    np.testing.assert_array_equal(F, C[[2, 0, 1]])
    np.testing.assert_array_equal(dff, 2.0 * F)


def test_nwb_mask_puts_each_pixel_where_the_movie_has_it(monkeypatch):
    # caiman numbers pixels in Fortran order; reading them in C order exported
    # transposed or scrambled masks while the ROI images were right
    monkeypatch.setattr(cnmf, "get_roi", labelled_masks)
    dims = (3, 5)
    A = np.zeros((dims[0] * dims[1], 1))
    A[np.ravel_multi_index((1, 3), dims, order="F"), 0] = 1.0

    im, _, roi_list, *_ = cnmf.component_outputs(
        sp.csc_matrix(A), np.ones((1, FRAMES)), [0], [], dims, 0.9, "nrg", False
    )

    assert np.argwhere(roi_list[0]["image_mask"]).tolist() == [[1, 3]]
    assert np.argwhere(~np.isnan(im[0])).tolist() == [[1, 3]]


def test_no_components_gives_empty_outputs_with_the_time_axis_kept():
    (
        im,
        iscell,
        roi_list,
        F,
        non_cell_roi,
        n_rois,
        n_noncell,
        order,
    ) = cnmf.component_outputs(
        None, None, [], [], DIMS, 0.9, "nrg", False, n_frames=FRAMES
    )

    assert im.shape == (0, *DIMS) and F.shape == (0, FRAMES)
    assert len(iscell) == 0 and roi_list == [] and (n_rois, n_noncell) == (0, 0)
    assert np.isnan(non_cell_roi).all()


@pytest.mark.parametrize("idx_good,idx_bad", [([], [0, 1, 2]), ([0], [1, 2])])
def test_rejected_ids_follow_the_accepted_ones_on_a_nan_background(
    idx_good, idx_bad, monkeypatch
):
    monkeypatch.setattr(cnmf, "get_roi", labelled_masks)
    A = sp.csc_matrix(np.eye(PIXELS)[:, :3])

    im, _, _, _, non_cell_roi, *_ = cnmf.component_outputs(
        A, np.ones((3, FRAMES)), idx_good, idx_bad, DIMS, 0.9, "nrg", False
    )

    assert [np.nanmax(row) for row in im] == [0, 1, 2]
    np.testing.assert_array_equal(non_cell_roi, np.nanmax(im[len(idx_good) :], axis=0))


def test_a_mask_count_that_differs_from_the_components_is_refused(monkeypatch):
    # get_roi now emits one mask per column, empty or not; a shorter list would
    # shift every row after the gap, so it is refused rather than realigned
    monkeypatch.setattr(cnmf, "get_roi", lambda A, *args: labelled_masks(A, *args)[1:])
    A = sp.csc_matrix(np.ones((PIXELS, 2)))
    C = np.ones((2, FRAMES))

    with pytest.raises(ValueError, match="one mask per"):
        cnmf.component_outputs(A, C, [0, 1], [], DIMS, 0.9, "nrg", False)


def test_get_roi_emits_an_empty_mask_for_an_empty_component():
    pytest.importorskip("skimage")
    dense = np.zeros((PIXELS, 2))
    dense[:4, 1] = 1.0  # component 0 has no pixels at all
    ims = cnmf.get_roi(sp.csc_matrix(dense), 0.9, "nrg", False, DIMS)

    assert len(ims) == 2
    assert not ims[0].any() and ims[1].any()
