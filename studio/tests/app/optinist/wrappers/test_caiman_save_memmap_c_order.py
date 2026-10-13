import os

import numpy as np
import pytest

from studio.app.optinist.wrappers.caiman.caiman_utils import save_memmap_c_order

caiman = pytest.importorskip("caiman")

D1, D2, T = 8, 6, 23


def motion_corrected_mmap(tmp_path):
    """A pixels x time, F-order float32 file named the way MotionCorrect names it."""
    path = str(tmp_path / f"mov_rig__d1_{D1}_d2_{D2}_d3_1_order_F_frames_{T}.mmap")
    values = np.random.default_rng(0).normal(100, 30, (D1 * D2, T)).astype(np.float32)
    out = np.memmap(path, mode="w+", dtype=np.float32, shape=values.shape, order="F")
    out[:] = values
    out.flush()
    del out
    return path


def test_same_file_as_caiman_save_memmap(tmp_path):
    source = motion_corrected_mmap(tmp_path)

    expected = caiman.save_memmap([source], base_name="a", order="C", border_to_0=0)
    # A batch size that does not divide the frame count
    actual = save_memmap_c_order([source], base_name="b", border_to_0=0, batch_size=7)

    assert os.path.basename(actual)[1:] == os.path.basename(expected)[1:]
    assert open(actual, "rb").read() == open(expected, "rb").read()


def test_other_cases_go_to_caiman(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(caiman, "save_memmap", lambda *a, **k: calls.append(k) or "x")
    source = motion_corrected_mmap(tmp_path)

    assert save_memmap_c_order([source], base_name="b", border_to_0=2) == "x"
    assert save_memmap_c_order([source, source], base_name="b") == "x"
    assert [c["border_to_0"] for c in calls] == [2, 0]
