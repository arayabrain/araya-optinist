"""lccd_detect validates parameters against the movie's shape before running
the algorithm, so a short movie with default settings fails fast with one
actionable error instead of an opaque crash deep in the pipeline.
"""
import importlib.util
import sys
import types

import numpy as np
import pytest
import scipy.sparse

from studio.app.common.core.workflow.workflow_params import read_default_params
from studio.app.common.dataclass import ImageData
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.wrappers.lccd import lccd_detection
from studio.app.optinist.wrappers.lccd.lccd_detection import (
    lccd_config,
    lccd_detect,
    validate_params,
)
from studio.app.optinist.wrappers.lccd.lccd_python.roi_integration import (
    filter_roi_by_area,
)

ROWS, COLS, N_TIME = 16, 12, 40
SHAPE = (ROWS, COLS, N_TIME)


def valid_params():
    return {
        "blob_detector": {
            "filtersize1": 10,
            "filtersize2": 4,
            "sigma": 1.25,
            "fsize": 9,
            "min_area": 4,
            "max_area": 16,
            "sparse": False,
        },
        "roi_integration": {
            "overlap_threshold": 0.25,
            "min_area": 4,
            "max_area": 16,
            "sparse": False,
        },
        "lccd": {"frame_divider": 10},
        "dff": {"f0_frames": 5, "f0_percentile": 8},
    }


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "output" / "default" / "uid" / "func"
    d.mkdir(parents=True)
    return str(d)


@pytest.fixture
def images(tmp_path):
    frames = np.random.default_rng(0).random((N_TIME, ROWS, COLS)).astype(np.float32)
    return ImageData(frames, output_dir=str(tmp_path), file_name="img")


@pytest.fixture
def lccd_stub(monkeypatch):
    """Three fixed ROIs; records the config LCCD is built with."""
    configs = []

    class FakeLCCD:
        def __init__(self, config):
            configs.append(config)

        def apply(self, D):
            roi = np.zeros((D.shape[0] * D.shape[1], 3))
            for i in range(3):
                roi[i * 4 : (i + 1) * 4, i] = 1
            return roi

    fake = types.ModuleType("lccd")
    fake.LCCD = FakeLCCD
    monkeypatch.setitem(
        sys.modules, f"{lccd_detection.__package__}.lccd_python.lccd", fake
    )
    return configs


def test_default_params_on_short_movie_fail():
    with pytest.raises(ValueError, match="frame_divider \\(100\\) is larger"):
        validate_params(read_default_params("lccd_cell_detection"), SHAPE)


def test_default_params_valid_from_100_frames(caplog):
    params = read_default_params("lccd_cell_detection")
    with caplog.at_level("WARNING"):
        validate_params(params, (512, 512, 100))
    assert "dff will be entirely NaN" in caplog.text

    with pytest.raises(ValueError, match="set frame_divider <= 99"):
        validate_params(params, (512, 512, 99))


def test_valid_params_pass_without_warnings(caplog):
    with caplog.at_level("WARNING"):
        validate_params(valid_params(), SHAPE)
    assert not caplog.records


def test_none_params_pass():
    validate_params(None, SHAPE)


@pytest.mark.parametrize(
    "section, key, value, expected",
    [
        ("lccd", "frame_divider", 0, "frame_divider must be >= 1"),
        ("lccd", "frame_divider", 41, "frame_divider (41) is larger"),
        ("blob_detector", "filtersize1", 0, "filtersize1 must be >= 1"),
        ("blob_detector", "filtersize2", 0, "filtersize2 must be >= 1"),
        ("blob_detector", "filtersize2", 10, "must be < filtersize1"),
        ("blob_detector", "sigma", 0, "sigma must be > 0"),
        ("blob_detector", "fsize", 0, "fsize (0) must be between 1"),
        ("blob_detector", "fsize", 13, "shorter side (12 pixels)"),
        ("roi_integration", "overlap_threshold", -0.1, "between 0 and 1"),
        ("roi_integration", "overlap_threshold", 1.1, "between 0 and 1"),
        ("dff", "f0_frames", 0, "f0_frames must be >= 1"),
        ("dff", "f0_percentile", -1, "between 0 and 100"),
        ("dff", "f0_percentile", 101, "between 0 and 100"),
        ("lccd", "frame_divider", 10.5, "must be an integer"),
        ("blob_detector", "filtersize1", 10.5, "must be an integer"),
        ("dff", "f0_frames", "100", "must be an integer"),
        ("lccd", "frame_divider", None, "must be an integer"),
        ("blob_detector", "sigma", "1.25", "sigma must be a finite number"),
        ("blob_detector", "sigma", float("nan"), "sigma must be a finite number"),
        ("blob_detector", "sigma", 10**400, "sigma must be a finite number"),
        ("roi_integration", "overlap_threshold", True, "must be a finite number"),
        (
            "blob_detector",
            "min_area",
            True,
            "blob_detector.min_area must be a finite number",
        ),
        (
            "roi_integration",
            "min_area",
            16,
            "roi_integration.min_area (16) must be < roi_integration.max_area (16)",
        ),
    ],
)
def test_single_rule_violations(section, key, value, expected):
    params = valid_params()
    params[section][key] = value
    with pytest.raises(ValueError, match="invalid LCCD parameters") as exc:
        validate_params(params, SHAPE)
    assert expected in str(exc.value)


@pytest.mark.parametrize(
    "section, key, value",
    [
        ("roi_integration", "overlap_threshold", 0),
        ("roi_integration", "overlap_threshold", 1),
        ("blob_detector", "max_area", ROWS * COLS * 10),
        ("blob_detector", "min_area", 4.5),
        ("lccd", "frame_divider", N_TIME),
    ],
)
def test_values_that_run_are_accepted(section, key, value):
    params = valid_params()
    params[section][key] = value
    validate_params(params, SHAPE)


def test_filtersize1_larger_than_frame_divider_warns_only(caplog):
    params = valid_params()
    params["blob_detector"]["filtersize1"] = 25
    with caplog.at_level("WARNING"):
        validate_params(params, SHAPE)
    assert "filtersize1 (25) is larger than frame_divider (10)" in caplog.text


def test_flat_params_report_an_area_error_once():
    with pytest.raises(ValueError) as exc:
        validate_params({"min_area": 16, "max_area": 16}, SHAPE)
    assert str(exc.value).count("min_area (16) must be < max_area (16)") == 1


def test_even_fsize_warns_only(caplog):
    params = valid_params()
    params["blob_detector"]["fsize"] = 8
    with caplog.at_level("WARNING"):
        validate_params(params, SHAPE)
    assert "odd size is recommended" in caplog.text


@pytest.mark.parametrize("f0_frames, warns", [(19, False), (20, True), (100, True)])
def test_all_nan_dff_warning_boundary(caplog, f0_frames, warns):
    params = valid_params()
    params["dff"]["f0_frames"] = f0_frames
    with caplog.at_level("WARNING"):
        validate_params(params, SHAPE)
    assert ("dff will be entirely NaN" in caplog.text) == warns


def test_each_stage_keeps_its_own_section():
    params = valid_params()
    params["blob_detector"]["max_area"] = 32
    params["roi_integration"]["sparse"] = True
    config = lccd_config(params)
    assert config["blob_detector"]["max_area"] == 32
    assert config["blob_detector"]["sparse"] is False
    assert config["roi_integration"]["max_area"] == 16
    assert config["roi_integration"]["sparse"] is True
    assert config["frame_divider"] == 10


def test_the_shipped_defaults_run_both_stages_sparse():
    config = lccd_config(read_default_params("lccd_cell_detection"))
    assert config["blob_detector"]["sparse"] is True
    assert config["roi_integration"]["sparse"] is True


def test_flat_params_feed_both_stages():
    config = lccd_config({"min_area": 4, "max_area": 16, "frame_divider": 10})
    for stage in ("blob_detector", "roi_integration"):
        assert config[stage]["max_area"] == 16


def test_lccd_detect_fails_before_reading_the_movie(images, output_dir, monkeypatch):
    def no_read(*args):
        raise AssertionError("LoadData ran")

    monkeypatch.setattr(lccd_detection, "LoadData", no_read)
    params = read_default_params("lccd_cell_detection")
    with pytest.raises(ValueError, match="invalid LCCD parameters"):
        lccd_detect(images, output_dir, params=params)


def test_lccd_detect_builds_lccd_per_stage(images, output_dir, lccd_stub):
    params = valid_params()
    params["blob_detector"]["max_area"] = 32
    lccd_detect(images, output_dir, params=params)
    [config] = lccd_stub
    assert config["blob_detector"]["max_area"] == 32
    assert config["roi_integration"]["max_area"] == 16


def test_lccd_detect_runs_on_non_square_frames(images, output_dir, lccd_stub):
    info = lccd_detect(images, output_dir, params=valid_params())
    assert info["fluorescence"].data.shape == (3, N_TIME)
    roi_masks = info["edit_roi_data"].roi_masks
    assert len(roi_masks) == 3
    assert roi_masks.shape == (ROWS, COLS)


class Undensifiable(scipy.sparse.csc_matrix):
    """A sparse LCCD result that fails if anything makes it dense."""

    def toarray(self, *args, **kwargs):
        raise AssertionError("the (pixels, ROIs) matrix was made dense")

    todense = toarray


def test_lccd_detect_reads_a_sparse_result_as_its_dense_form(
    images, output_dir, monkeypatch
):
    rng = np.random.default_rng(3)
    rois = np.zeros((ROWS * COLS, 6))
    for i in range(rois.shape[1]):
        rois[rng.choice(ROWS * COLS, rng.integers(4, 17), replace=False), i] = 1

    def run(as_result):
        class FakeLCCD:
            def __init__(self, config):
                pass

            def apply(self, D):
                return as_result(rois)

        fake = types.ModuleType("lccd")
        fake.LCCD = FakeLCCD
        monkeypatch.setitem(
            sys.modules, f"{lccd_detection.__package__}.lccd_python.lccd", fake
        )
        return lccd_detect(images, output_dir, params=valid_params())

    dense, sparse = run(np.array), run(Undensifiable)

    for key in ("fluorescence", "dff", "all_roi", "cell_roi"):
        np.testing.assert_array_equal(sparse[key].data, dense[key].data)
    for name in ("indptr", "indices"):
        np.testing.assert_array_equal(
            getattr(sparse["edit_roi_data"].roi_masks, name),
            getattr(dense["edit_roi_data"].roi_masks, name),
        )
    (dense_table,) = dense["nwbfile"][NWBDATASET.ROI].values()
    (sparse_table,) = sparse["nwbfile"][NWBDATASET.ROI].values()
    assert len(sparse_table["roi_list"]) == len(dense_table["roi_list"]) == 6
    for d, s in zip(dense_table["roi_list"], sparse_table["roi_list"]):
        np.testing.assert_array_equal(s["pixel_mask"], d["pixel_mask"])


@pytest.fixture
def real_lccd(monkeypatch):
    """The real LCCD.apply and RoiIntegration over scripted blob detector
    results, one per chunk (skimage is not installed here)."""
    package = f"{lccd_detection.__package__}.lccd_python"

    def build(
        chunks,
        blob_sparse=False,
        roi_sparse=False,
        roi_area=(1, 100),
        overlap_threshold=0.25,
    ):
        class ScriptedBlobDetector:
            def __init__(self, **kwargs):
                self.chunks = iter(chunks)

            def apply(self, tm):
                roi = next(self.chunks)
                if roi is None:
                    raise ValueError("No roi region found. Revise config and input.")
                return scipy.sparse.csc_matrix(roi) if blob_sparse else roi

        fake = types.ModuleType("blob_detector")
        fake.BlobDetector = ScriptedBlobDetector
        monkeypatch.setitem(sys.modules, f"{package}.blob_detector", fake)
        monkeypatch.delitem(sys.modules, f"{package}.lccd", raising=False)
        from studio.app.optinist.wrappers.lccd.lccd_python.lccd import LCCD

        config = {
            "blob_detector": {},
            "roi_integration": {
                "overlap_threshold": overlap_threshold,
                "min_area": roi_area[0],
                "max_area": roi_area[1],
                "sparse": roi_sparse,
            },
            "frame_divider": 10,
        }
        return LCCD(config).apply(np.zeros((4, 4, 10 * len(chunks))))

    return build


def mask(*pixel_ranges, n_pixels=16):
    roi = np.zeros((n_pixels, len(pixel_ranges)))
    for i, (start, stop) in enumerate(pixel_ranges):
        roi[start:stop, i] = 1
    return roi


def test_an_empty_last_chunk_keeps_earlier_rois(real_lccd):
    roi = real_lccd([mask((0, 4)), None])
    assert roi.shape == (16, 1)
    assert roi.toarray()[:4, 0].all()


def test_an_empty_movie_reports_no_roi(real_lccd):
    with pytest.raises(ValueError, match="No roi region found"):
        real_lccd([None, None])


@pytest.mark.parametrize(
    "blob_sparse, roi_sparse", [(False, False), (False, True), (True, False)]
)
@pytest.mark.parametrize("n_chunks", [1, 2])
def test_every_sparse_setting_gives_a_sparse_result(
    real_lccd, blob_sparse, roi_sparse, n_chunks
):
    chunks = [mask((0, 4)), mask((8, 12))][:n_chunks]
    roi = real_lccd(chunks, blob_sparse=blob_sparse, roi_sparse=roi_sparse)
    assert scipy.sparse.issparse(roi) and roi.format == "csc"
    assert roi.shape == (16, n_chunks)


@pytest.mark.parametrize("roi_sparse", [False, True])
def test_one_chunk_applies_the_integration_area_limits(real_lccd, roi_sparse):
    roi = real_lccd([mask((0, 3), (4, 12))], roi_sparse=roi_sparse, roi_area=(2, 4))
    assert roi.shape == (16, 1)
    assert roi.toarray()[:3, 0].all()


def overlapping_chunks(seed, n_chunks=4, n_rois=3):
    """Per chunk, ROIs of 3 to 7 of the 16 pixels, overlapping at random."""
    rng = np.random.default_rng(seed)
    chunks = []
    for _ in range(n_chunks):
        roi = np.zeros((16, n_rois))
        for i in range(n_rois):
            roi[rng.choice(16, rng.integers(3, 8), replace=False), i] = 1
        chunks.append(roi)
    return chunks


@pytest.mark.parametrize("overlap_threshold", [0.2, 0.25, 0.3])
@pytest.mark.parametrize("seed", range(8))
def test_dense_and_sparse_integration_find_the_same_rois(
    real_lccd, seed, overlap_threshold
):
    dense = real_lccd(overlapping_chunks(seed), overlap_threshold=overlap_threshold)
    for blob_sparse in (False, True):
        sparse = real_lccd(
            overlapping_chunks(seed),
            blob_sparse=blob_sparse,
            roi_sparse=True,
            overlap_threshold=overlap_threshold,
        )
        np.testing.assert_array_equal(sparse.toarray(), dense.toarray())


@pytest.mark.parametrize(
    "overlap_threshold, earlier, later, n_pixels",
    [
        (0.2, (0, 10), (9, 14), 16),  # overlap 1 of the later ROI's 5 pixels
        (0.3, (0, 20), (17, 27), 27),  # overlap 3 of the later ROI's 10 pixels
    ],
)
def test_an_overlap_ratio_equal_to_the_threshold_integrates_alike(
    real_lccd, overlap_threshold, earlier, later, n_pixels
):
    # 0.2 and 0.3 have no exact binary form: the ratio must round the same way
    # in both modes for the comparison with the threshold to agree
    chunks = [mask(earlier, n_pixels=n_pixels), mask(later, n_pixels=n_pixels)]
    dense = real_lccd(chunks, overlap_threshold=overlap_threshold)
    sparse = real_lccd(chunks, roi_sparse=True, overlap_threshold=overlap_threshold)
    np.testing.assert_array_equal(sparse.toarray(), dense.toarray())


@pytest.fixture
def oval_filter(monkeypatch):
    """The real oval_filter over a stand-in skimage (not installed here) whose
    regionprops passes every labelled region as a round blob."""

    def regionprops(label_mat):
        return [
            types.SimpleNamespace(
                label=label,
                area=1,
                axis_major_length=1,
                axis_minor_length=1,
                eccentricity=0,
            )
            for label in np.unique(label_mat[label_mat > 0])
        ]

    skimage = types.ModuleType("skimage")
    skimage.measure = types.SimpleNamespace(regionprops=regionprops)
    monkeypatch.setitem(sys.modules, "skimage", skimage)
    # loaded unregistered, so no module bound to the stand-in outlives the test
    spec = importlib.util.find_spec(
        f"{lccd_detection.__package__}.lccd_python.oval_filter"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.oval_filter


@pytest.mark.parametrize(
    "regions",
    [
        [(slice(1, 3), slice(1, 3))],  # one blob
        [(slice(1, 3), slice(1, 3)), (slice(5, 7), slice(5, 7))],  # equal areas
        [(slice(1, 3), slice(1, 3)), (slice(4, 7), slice(4, 7))],  # different
    ],
)
def test_sparse_oval_filter_gives_the_dense_rois(oval_filter, regions):
    label_mat = np.zeros((8, 8), dtype=int)
    for label, region in enumerate(regions, start=1):
        label_mat[region] = label
    dense = oval_filter(label_mat, sparse=False)
    sparse = oval_filter(label_mat, sparse=True)
    assert sparse.format == "csc" and sparse.dtype == dense.dtype
    np.testing.assert_array_equal(sparse.toarray(), dense)


@pytest.mark.parametrize("sparse", [False, True])
def test_filter_roi_by_area_returns_empty_when_all_filtered(sparse):
    roi = np.ones((16, 2))
    if sparse:
        roi = scipy.sparse.csc_matrix(roi)
    assert filter_roi_by_area(roi, 1, 4).shape == (16, 0)
