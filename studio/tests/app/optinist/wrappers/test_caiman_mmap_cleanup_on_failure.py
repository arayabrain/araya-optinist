"""A CaImAn node that fails leaves no memmap, split part or temp dir behind.
caiman itself is stubbed (the app image has none), as in
test_caiman_cnmf_memmap_from_files.py; the wrappers' own file handling runs."""

import os
import sys
import types
from contextlib import contextmanager

import numpy as np
import pytest
import tifffile

from studio.app.common.dataclass import ImageData
from studio.app.dir_path import DIRPATH


class _Params:
    def __init__(self):
        self.motion = {}

    def change_params(self, *args, **kwargs):
        return self

    def get_group(self, name):
        return {}

    def get(self, group, key):
        return False


def _module(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


@pytest.fixture
def caiman(monkeypatch, tmp_path):
    """A stand-in caiman package: fit and motion_correct fail on demand; the
    temp dir and memmap naming helpers behave as caiman's do."""
    pytest.importorskip("tifffile")
    state = {"fit_error": RuntimeError("fit failed"), "mc_calls": 0}
    temp_root = tmp_path / "caiman_temp"
    temp_root.mkdir()
    monkeypatch.delenv("CAIMAN_TEMP", raising=False)

    class CNMF:
        def __init__(self, *args, **kwargs):
            pass

        def fit(self, images):
            raise state["fit_error"]

    class MotionCorrect:
        def __init__(self, fnames, dview=None, **kwargs):
            state["mc_calls"] += 1
            self.mmap_file = []

        def motion_correct(self, save_movie=True):
            tempdir = os.environ["CAIMAN_TEMP"]
            self.mmap_file = [os.path.join(tempdir, "mc_d1_9_d2_7.mmap")]
            with open(self.mmap_file[0], "wb") as f:
                f.write(b"\0" * 64)
            raise RuntimeError("motion correction failed")

    paths = _module(
        "caiman.paths",
        get_tempdir=lambda: os.environ.get("CAIMAN_TEMP", str(temp_root)),
        caiman_datadir=lambda: str(tmp_path),
        memmap_frames_filename=lambda base, dims, T, order: (
            f"{base}_d1_{dims[0]}_d2_{dims[1]}_d3_1_order_{order}_frames_{T}.mmap"
        ),
    )
    modules = {
        "caiman": _module("caiman", load_memmap=None, save_memmap=None, paths=paths),
        "caiman.paths": paths,
        "caiman.mmapping": _module(
            "caiman.mmapping",
            prepare_shape=lambda shape: tuple(np.uint64(n) for n in shape),
        ),
        "caiman.motion_correction": _module(
            "caiman.motion_correction", MotionCorrect=MotionCorrect
        ),
        "caiman.base": _module("caiman.base"),
        "caiman.base.rois": _module("caiman.base.rois", register_multisession=None),
        "caiman.source_extraction": _module("caiman.source_extraction"),
        "caiman.source_extraction.cnmf": _module(
            "caiman.source_extraction.cnmf",
            cnmf=_module("caiman.source_extraction.cnmf.cnmf", CNMF=CNMF),
            online_cnmf=None,
        ),
        "caiman.source_extraction.cnmf.params": _module(
            "caiman.source_extraction.cnmf.params", CNMFParams=_Params
        ),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    @contextmanager
    def no_cluster(*args, **kwargs):
        yield None, 1

    from studio.app.optinist.wrappers.caiman import (
        cnmf,
        cnmf_multisession,
        motion_correction,
    )

    for module in (cnmf, cnmf_multisession):
        monkeypatch.setattr(module, "util_download_model_files", lambda: None)
        monkeypatch.setattr(module, "caiman_cluster", no_cluster)
    monkeypatch.setattr(motion_correction, "caiman_cluster", no_cluster)
    state["temp_root"] = temp_root
    return state


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    """A small movie and a node output dir under a fresh OUTPUT_DIR."""
    monkeypatch.setattr(DIRPATH, "OUTPUT_DIR", str(tmp_path / "output"))
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    movie = str(input_dir / "movie.tif")
    rng = np.random.default_rng(0)
    tifffile.imwrite(movie, rng.integers(0, 500, (6, 9, 7)).astype(np.uint16))

    def node(function_id):
        output_dir = os.path.join(DIRPATH.OUTPUT_DIR, "ws", "uid1", function_id)
        os.makedirs(output_dir)
        return output_dir

    return {"movie": movie, "node": node, "input_dir": input_dir}


def _files(root, suffix):
    return [
        os.path.join(d, f)
        for d, _, files in os.walk(root)
        for f in files
        if f.endswith(suffix)
    ]


@pytest.mark.usefixtures("caiman")
def test_cnmf_failure_leaves_no_memmap(experiment):
    from studio.app.optinist.wrappers.caiman.cnmf import caiman_cnmf

    output_dir = experiment["node"]("caiman_cnmf")
    with pytest.raises(RuntimeError, match="fit failed"):
        caiman_cnmf(ImageData([experiment["movie"]]), output_dir, {})
    assert _files(experiment["input_dir"], ".mmap") == []
    assert _files(output_dir, ".mmap") == []


@pytest.mark.usefixtures("caiman")
def test_multisession_failure_leaves_no_memmap_or_split_parts(experiment):
    from studio.app.optinist.wrappers.caiman.cnmf_multisession import (
        caiman_cnmf_multisession,
    )

    output_dir = experiment["node"]("caiman_cnmf_multisession")
    with pytest.raises(RuntimeError, match="fit failed"):
        caiman_cnmf_multisession(
            ImageData([experiment["movie"]]), output_dir, {"n_reg_files": 2}
        )
    assert _files(output_dir, ".mmap") == []
    assert not os.path.exists(os.path.join(output_dir, "image_split"))


def test_mc_failure_removes_its_temp_dir(caiman, experiment):
    from studio.app.optinist.wrappers.caiman.motion_correction import caiman_mc

    output_dir = experiment["node"]("caiman_mc")
    with pytest.raises(RuntimeError, match="motion correction failed"):
        caiman_mc(ImageData([experiment["movie"]]), output_dir, {})
    assert caiman["mc_calls"] == 1
    assert os.listdir(caiman["temp_root"]) == []
    assert "CAIMAN_TEMP" not in os.environ


def test_mc_refuses_before_motion_correct_when_the_reserve_would_go(
    caiman, experiment, monkeypatch
):
    import errno
    from types import SimpleNamespace

    from studio.app.common.core.storage import local_disk_guard as guard
    from studio.app.optinist.wrappers.caiman.motion_correction import caiman_mc

    monkeypatch.setenv(guard.MIN_FREE_DISK_ENV, "8")
    monkeypatch.setattr(
        guard.shutil,
        "disk_usage",
        lambda p: SimpleNamespace(free=8 * guard.GIB + 1000, total=0, used=0),
    )
    output_dir = experiment["node"]("caiman_mc")
    with pytest.raises(OSError) as info:
        caiman_mc(ImageData([experiment["movie"]]), output_dir, {})
    assert info.value.errno == errno.ENOSPC
    assert "must stay free" in str(info.value)
    assert caiman["mc_calls"] == 0
    assert os.listdir(caiman["temp_root"]) == []


def test_mc_budgets_the_memmap_its_copy_and_the_output_tiff(
    caiman, experiment, monkeypatch
):
    import errno
    from types import SimpleNamespace

    from studio.app.common.core.storage import local_disk_guard as guard
    from studio.app.optinist.wrappers.caiman.motion_correction import (
        _movie_float32_bytes,
        caiman_mc,
    )

    nbytes = _movie_float32_bytes(experiment["movie"])
    monkeypatch.setenv(guard.MIN_FREE_DISK_ENV, "8")
    monkeypatch.setattr(
        guard.shutil,
        "disk_usage",
        lambda p: SimpleNamespace(free=8 * guard.GIB + 2 * nbytes + 1, total=0, used=0),
    )
    output_dir = experiment["node"]("caiman_mc")
    with pytest.raises(OSError) as info:
        caiman_mc(ImageData([experiment["movie"]]), output_dir, {})
    assert info.value.errno == errno.ENOSPC
    assert caiman["mc_calls"] == 0


def test_mc_skips_the_check_when_a_header_cannot_be_read(
    caiman, experiment, monkeypatch
):
    from types import SimpleNamespace

    from studio.app.common.core.storage import local_disk_guard as guard
    from studio.app.optinist.wrappers.caiman.motion_correction import caiman_mc

    monkeypatch.setenv(guard.MIN_FREE_DISK_ENV, "8")
    monkeypatch.setattr(
        guard.shutil, "disk_usage", lambda p: SimpleNamespace(free=0, total=0, used=0)
    )
    output_dir = experiment["node"]("caiman_mc")
    with pytest.raises(RuntimeError, match="motion correction failed"):
        caiman_mc(
            ImageData([str(experiment["input_dir"] / "none.tif")]), output_dir, {}
        )
    assert caiman["mc_calls"] == 1
