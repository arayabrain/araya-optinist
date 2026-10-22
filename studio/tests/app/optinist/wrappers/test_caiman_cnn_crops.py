import contextlib
import sys
import types

import numpy as np
import pytest
import scipy.ndimage
import scipy.sparse as sp

from studio.app.optinist.wrappers.caiman import caiman_utils, cnmf
from studio.app.optinist.wrappers.caiman.caiman_utils import (
    cnn_crops,
    cnn_crops_copied,
    evaluate_components_cnn,
)


def caimans_crops(A, dims, gSig, patch_size=50):
    """The crops as CaImAn 1.11.2-1.12.2's evaluate_components_CNN slices them"""
    half_crop = np.minimum(gSig[0] * 4 + 1, patch_size), np.minimum(
        gSig[1] * 4 + 1, patch_size
    )
    dims = np.array(dims)
    coms = [
        scipy.ndimage.center_of_mass(mm.toarray().reshape(dims, order="F"))
        for mm in A.tocsc().T
    ]
    coms = np.maximum(coms, half_crop)
    coms = np.array([np.minimum(cms, dims - half_crop) for cms in coms]).astype(int)
    return [
        mm.toarray().reshape(dims, order="F")[
            com[0] - half_crop[0] : com[0] + half_crop[0],
            com[1] - half_crop[1] : com[1] + half_crop[1],
        ]
        for mm, com in zip(A.tocsc().T, coms)
    ]


def footprints(dims, k, seed, dtype=np.float64):
    """k Gaussian footprints, pixels in Fortran order as CaImAn's A (float64
    after a patch-mode fit, float32 with rf None), the first at the border so
    its crop is clamped"""
    rng = np.random.default_rng(seed)
    d1, d2 = dims
    yy, xx = np.mgrid[0:d1, 0:d2]
    cols = []
    for i in range(k):
        cy, cx = (0.0, d2 - 1.0) if i == 0 else (rng.uniform(0, d1), rng.uniform(0, d2))
        s = rng.uniform(2, 6)
        a = np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * s * s))
        a[a < 0.05] = 0
        cols.append(a.ravel(order="F"))
    return sp.csc_matrix(np.stack(cols, axis=1).astype(dtype))


CASES = [((128, 128), 30, [4, 4]), ((96, 160), 12, [3, 5]), ((64, 64), 1, [4, 4])]


@pytest.mark.parametrize("dtype", [np.float64, np.float32])
@pytest.mark.parametrize("dims,k,gSig", CASES)
def test_crops_are_caimans_but_hold_only_themselves(dims, k, gSig, dtype):
    A = footprints(dims, k, seed=k, dtype=dtype)

    ours, theirs = cnn_crops(A, dims, gSig), caimans_crops(A, dims, gSig)

    assert len(ours) == len(theirs) == k
    frame_bytes = dims[0] * dims[1] * np.dtype(dtype).itemsize
    for crop, view in zip(ours, theirs):
        assert np.array_equal(crop, view) and crop.dtype == view.dtype == dtype
        # CaImAn's crop keeps its whole frame; the copy only itself
        assert view.base is not None and view.base.nbytes == frame_bytes
        assert crop.base is None and crop.nbytes < frame_bytes
        # the same memory order, so the norm, summed in that order, is the same
        assert crop.flags.f_contiguous
        assert np.linalg.norm(crop) == np.linalg.norm(view)


class StubModel:
    def predict(self, x, batch_size=32, verbose=0):
        return x.reshape(len(x), -1).sum(axis=1, keepdims=True)


def stub_resize(im, size):
    return np.resize(im, size)


def test_evaluation_gives_caimans_crops_and_predictions(monkeypatch):
    """The whole copied function, with cv2's resize and the model stubbed (not
    in this environment): crops normalised, resized and classified as CaImAn's"""
    monkeypatch.setitem(sys.modules, "cv2", types.SimpleNamespace(resize=stub_resize))
    paths = types.ModuleType("caiman.paths")
    paths.caiman_datadir = lambda: "caiman_data"
    monkeypatch.setitem(sys.modules, "caiman", types.ModuleType("caiman"))
    monkeypatch.setitem(sys.modules, "caiman.paths", paths)
    keras_models = types.ModuleType("tensorflow.keras.models")
    keras_models.model_from_json = None
    for name in ("tensorflow", "tensorflow.keras"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, keras_models.__name__, keras_models)
    dims, k, gSig = CASES[1]
    A = footprints(dims, k, seed=k)

    predictions, final_crops = evaluate_components_cnn(
        A, dims, gSig, loaded_model=StubModel()
    )

    expected = np.array(
        [
            stub_resize(im / np.linalg.norm(im), (50, 50))
            for im in caimans_crops(A, dims, gSig)
        ]
    )
    assert np.array_equal(final_crops, expected)
    assert np.array_equal(predictions, StubModel().predict(expected[..., np.newaxis]))


def caiman_modules(monkeypatch, version):
    """Stub caiman with the two modules that name evaluate_components_CNN"""

    def caimans(*args, **kwargs):
        raise AssertionError("CaImAn's own evaluate_components_CNN")

    caiman = types.ModuleType("caiman")
    caiman.__version__ = version
    evaluation = types.ModuleType("caiman.components_evaluation")
    estimates = types.ModuleType("caiman.source_extraction.cnmf.estimates")
    for module in (evaluation, estimates):
        module.evaluate_components_CNN = caimans
    modules = {
        "caiman": caiman,
        "caiman.components_evaluation": evaluation,
        "caiman.source_extraction": types.ModuleType("caiman.source_extraction"),
        "caiman.source_extraction.cnmf": types.ModuleType(
            "caiman.source_extraction.cnmf"
        ),
        "caiman.source_extraction.cnmf.estimates": estimates,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return modules, caimans


NAMING = ["caiman.components_evaluation", "caiman.source_extraction.cnmf.estimates"]


@pytest.mark.parametrize("version", caiman_utils.CNN_CROPS_VERIFIED_CAIMAN)
def test_the_copy_replaces_caimans_only_while_active(monkeypatch, version):
    modules, caimans = caiman_modules(monkeypatch, version)

    with cnn_crops_copied():
        for name in NAMING:
            assert modules[name].evaluate_components_CNN is evaluate_components_cnn
    for name in NAMING:
        assert modules[name].evaluate_components_CNN is caimans


def test_caimans_is_restored_when_the_evaluation_fails(monkeypatch):
    modules, caimans = caiman_modules(monkeypatch, "1.11.4")

    with pytest.raises(RuntimeError), cnn_crops_copied():
        raise RuntimeError("evaluation failed")
    for name in NAMING:
        assert modules[name].evaluate_components_CNN is caimans


@pytest.mark.parametrize("version", ["1.11.1", "1.13.2"])
def test_another_caiman_keeps_its_own_with_a_warning(monkeypatch, version):
    modules, caimans = caiman_modules(monkeypatch, version)
    warnings = []
    monkeypatch.setattr(caiman_utils.logger, "warning", warnings.append)

    with cnn_crops_copied():
        for name in NAMING:
            assert modules[name].evaluate_components_CNN is caimans
    assert len(warnings) == 1 and version in warnings[0]


class Evaluated(Exception):
    pass


def test_caiman_cnmf_evaluates_with_the_copy(monkeypatch):
    """caiman_cnmf, CaImAn stubbed up to its component evaluation"""
    modules, _ = caiman_modules(monkeypatch, "1.11.4")
    evaluation = modules["caiman.components_evaluation"]

    class CNMFParams:
        def change_params(self, _):
            pass

    class CNMF:
        def __init__(self, params=None, **_):
            self.params = params
            self.estimates = types.SimpleNamespace(
                A=sp.csc_matrix(np.ones((6, 2))), evaluate_components=self.evaluate
            )

        def fit(self, _):
            return self

        def evaluate(self, *_, **__):
            raise Evaluated(evaluation.evaluate_components_CNN)

    cnmf_module = modules["caiman.source_extraction.cnmf"]
    cnmf_module.cnmf = types.SimpleNamespace(CNMF=CNMF)
    cnmf_module.online_cnmf = None
    params_module = types.ModuleType("caiman.source_extraction.cnmf.params")
    params_module.CNMFParams = CNMFParams
    monkeypatch.setitem(sys.modules, params_module.__name__, params_module)
    monkeypatch.setattr(cnmf, "util_download_model_files", lambda: None)
    monkeypatch.setattr(
        cnmf, "util_get_image_memmap_from_files", lambda *_: (None, (2, 3), "m.mmap")
    )
    monkeypatch.setattr(
        cnmf, "caiman_cluster", lambda _: contextlib.nullcontext((None, 1))
    )

    with pytest.raises(Evaluated) as evaluated:
        cnmf.caiman_cnmf(
            images=types.SimpleNamespace(path="movie.tif"),
            output_dir="1/abc/caiman_cnmf_x",
            params={},
        )

    assert evaluated.value.args[0] is evaluate_components_cnn
