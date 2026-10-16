import os
import re
import sys
import types

import numpy as np
import pytest

from studio.app.optinist.wrappers.caiman.caiman_utils import (
    C_ORDER_BLOCK_BYTES,
    save_memmap_c_order,
)

D1, D2, T = 8, 6, 23
# one block, several blocks of 5 pixel rows, and a budget below one row
BLOCK_BYTES = [C_ORDER_BLOCK_BYTES, 4 * T * 5, 1]


def motion_corrected_mmap(tmp_path):
    """A pixels x time, F-order float32 file named the way MotionCorrect names it."""
    path = str(tmp_path / f"mov_rig__d1_{D1}_d2_{D2}_d3_1_order_F_frames_{T}.mmap")
    values = np.random.default_rng(0).normal(100, 30, (D1 * D2, T)).astype(np.float32)
    values[0, :3] = [np.nan, np.inf, -0.0]
    out = np.memmap(path, mode="w+", dtype=np.float32, shape=values.shape, order="F")
    out[:] = values
    out.flush()
    del out
    return path


@pytest.fixture
def fake_caiman(tmp_path, monkeypatch):
    """CaImAn 1.11.4's naming and loading helpers, so this runs without CaImAn."""
    temp_dir = tmp_path / "caiman_temp"
    temp_dir.mkdir()

    def fn_relocated(fn, force_temp=False):
        if os.path.split(fn)[0] == "" or force_temp:
            return os.path.join(str(temp_dir), os.path.split(fn)[1])
        return fn

    def generate_fname_tot(base_name, dims, order):
        d3 = dims[2] if len(dims) == 3 else 1
        name = f"{base_name}_d1_{dims[0]}_d2_{dims[1]}_d3_{d3}_order_{order}"
        return fn_relocated(name, force_temp=True)

    def load_memmap(filename, mode="r"):
        d1, d2, d3, order, T = re.search(
            r"_d1_(\d+)_d2_(\d+)_d3_(\d+)_order_(\w)_frames_(\d+)", filename
        ).groups()
        d1, d2, d3, T = int(d1), int(d2), int(d3), int(T)
        dims = (d1, d2) if d3 == 1 else (d1, d2, d3)
        Yr = np.memmap(filename, mode=mode, dtype=np.float32, order=order)
        return Yr.reshape((d1 * d2 * d3, T), order=order), dims, T

    calls = []
    caiman = types.ModuleType("caiman")
    caiman.__version__ = "1.11.4"
    caiman.save_memmap = lambda *a, **k: calls.append(k) or "caiman"
    caiman.mmapping = types.SimpleNamespace(
        load_memmap=load_memmap,
        prepare_shape=lambda shape: tuple(np.uint64(n) for n in shape),
    )
    caiman.paths = types.SimpleNamespace(
        fn_relocated=fn_relocated, generate_fname_tot=generate_fname_tot
    )
    for name, module in [
        ("caiman", caiman),
        ("caiman.mmapping", caiman.mmapping),
        ("caiman.paths", caiman.paths),
    ]:
        monkeypatch.setitem(sys.modules, name, module)
    return types.SimpleNamespace(module=caiman, calls=calls, temp_dir=temp_dir)


@pytest.mark.parametrize("block_bytes", BLOCK_BYTES)
def test_values_and_name_are_save_memmaps(tmp_path, fake_caiman, block_bytes):
    source = motion_corrected_mmap(tmp_path)
    values = np.memmap(
        source, mode="r", dtype=np.float32, shape=(D1 * D2, T), order="F"
    )
    # caiman 1.11.4 save_memmap: ascontiguousarray(Yr) + 0.0001 + add_to_movie
    expected = np.ascontiguousarray(values) + np.float32(0.0001) + np.float32(0.0)

    path = save_memmap_c_order([source], base_name="mc", block_bytes=block_bytes)

    name = f"mc_d1_{D1}_d2_{D2}_d3_1_order_C_frames_{T}.mmap"
    assert path == str(fake_caiman.temp_dir / name)
    assert open(path, "rb").read() == expected.tobytes()
    assert fake_caiman.calls == []


def test_other_inputs_and_other_caiman_releases_go_to_caiman(tmp_path, fake_caiman):
    source = motion_corrected_mmap(tmp_path)

    assert save_memmap_c_order([source], base_name="b", border_to_0=2) == "caiman"
    assert save_memmap_c_order([source, source], base_name="b") == "caiman"
    tif = str(tmp_path / "mov.tif")
    assert save_memmap_c_order([tif], base_name="b") == "caiman"
    fake_caiman.module.__version__ = "1.13.0"  # rewrote the mirrored line
    assert save_memmap_c_order([source], base_name="b") == "caiman"
    assert [c["border_to_0"] for c in fake_caiman.calls] == [2, 0, 0, 0]

    fake_caiman.module.__version__ = "1.12.2"  # same save_memmap as 1.11.4
    assert save_memmap_c_order([source], base_name="b") != "caiman"


def test_a_failed_conversion_leaves_no_memmap(tmp_path, fake_caiman, monkeypatch):
    source = motion_corrected_mmap(tmp_path)
    load_memmap = fake_caiman.module.mmapping.load_memmap

    class FailsAfterOneBlock:
        def __init__(self, Yr):
            self.Yr, self.reads = Yr, 0

        def __getitem__(self, key):
            self.reads += 1
            if self.reads > 1:
                raise OSError("disk full")
            return self.Yr[key]

    def failing_load(filename):
        Yr, dims, T = load_memmap(filename)
        return FailsAfterOneBlock(Yr), dims, T

    monkeypatch.setattr(fake_caiman.module.mmapping, "load_memmap", failing_load)
    with pytest.raises(OSError, match="disk full"):
        save_memmap_c_order([source], base_name="mc", block_bytes=4 * T * 5)
    assert list(fake_caiman.temp_dir.iterdir()) == []


@pytest.mark.parametrize("block_bytes", BLOCK_BYTES)
def test_same_file_as_caiman_save_memmap(tmp_path, monkeypatch, block_bytes):
    caiman = pytest.importorskip("caiman")
    source = motion_corrected_mmap(tmp_path)
    expected_dir, actual_dir = tmp_path / "expected", tmp_path / "actual"
    expected_dir.mkdir()
    actual_dir.mkdir()

    monkeypatch.setenv("CAIMAN_TEMP", str(expected_dir))
    expected = caiman.save_memmap([source], base_name="mc", order="C", border_to_0=0)
    monkeypatch.setenv("CAIMAN_TEMP", str(actual_dir))
    actual = save_memmap_c_order([source], base_name="mc", block_bytes=block_bytes)

    assert os.path.dirname(expected) == str(expected_dir)
    assert os.path.dirname(actual) == str(actual_dir)
    assert os.path.basename(actual) == os.path.basename(expected)
    assert open(actual, "rb").read() == open(expected, "rb").read()


def test_a_full_disk_raises_and_leaves_no_memmap(tmp_path, fake_caiman, monkeypatch):
    # A memory-mapped output would die of SIGBUS here instead
    from studio.app.optinist.wrappers.caiman import caiman_utils

    source = motion_corrected_mmap(tmp_path)
    writes = []

    def write_block(f, block):
        writes.append(len(block))
        if len(writes) > 1:
            raise OSError(28, "No space left on device")
        block.tofile(f)

    monkeypatch.setattr(caiman_utils, "_write_block", write_block)
    with pytest.raises(OSError, match="No space left"):
        save_memmap_c_order([source], base_name="mc", block_bytes=4 * T * 5)
    assert list(fake_caiman.temp_dir.iterdir()) == []


def test_blocks_stay_within_the_byte_budget(tmp_path, fake_caiman, monkeypatch):
    source = motion_corrected_mmap(tmp_path)
    load_memmap = fake_caiman.module.mmapping.load_memmap
    rows_read = []

    class Recording:
        def __init__(self, Yr):
            self.Yr = Yr

        def __getitem__(self, key):
            block = self.Yr[key]
            rows_read.append(len(block))
            return block

    def recording_load(filename):
        Yr, dims, T = load_memmap(filename)
        return Recording(Yr), dims, T

    monkeypatch.setattr(fake_caiman.module.mmapping, "load_memmap", recording_load)
    save_memmap_c_order([source], base_name="mc", block_bytes=4 * T * 5)
    assert max(rows_read) * T * 4 <= 4 * T * 5
    assert sum(rows_read) == D1 * D2
