"""TIFF preview windows: ``read_tiff_frames`` and ``save_tiff2json``."""

import os
import tracemalloc
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
import tifffile

from studio.app.common.core.utils import json_writer
from studio.app.common.core.utils.json_writer import (
    TiffPreviewTooLargeError,
    read_tiff_frames,
    save_tiff2json,
)
from studio.app.dir_path import DIRPATH

# PackBits-compressed, 1000 x 128 x 128; writing PackBits needs imagecodecs
PACKBITS_TIFF = os.path.join(
    DIRPATH.ROOT_DIR, "studio", "test_data", "input", "1", "test.tif"
)

RNG = np.random.default_rng(0)
STACK_3D = RNG.integers(0, 4000, (20, 8, 6), dtype=np.uint16)
STACK_4D = RNG.integers(0, 4000, (5, 2, 8, 6), dtype=np.uint16)
STACK_RGB = RNG.integers(0, 255, (4, 8, 6, 3), dtype=np.uint8)
VOLUME = RNG.integers(0, 4000, (4, 16, 16), dtype=np.uint16)

# The comment names the read each layout takes in read_tiff_frames
LAYOUTS = {
    "contiguous": dict(data=STACK_3D),  # memory-mapped
    "big_endian": dict(data=STACK_3D, byteorder=">"),  # memory-mapped
    "single_frame": dict(data=STACK_3D[0]),  # memory-mapped, 2D
    "imagej_hyperstack": dict(  # memory-mapped, 4D
        data=STACK_4D, imagej=True, metadata={"axes": "TCYX"}
    ),
    "compressed": dict(data=STACK_3D, compression="zlib"),  # page by page
    "compressed_rgb": dict(  # page by page, 3 samples per pixel
        data=STACK_RGB, photometric="rgb", compression="zlib"
    ),
    "compressed_hyperstack": dict(  # page by page, 2 pages per frame
        data=STACK_4D, compression="zlib"
    ),
    "volumetric": dict(  # decoded whole: all 4 frames are in one page
        data=VOLUME,
        volumetric=True,
        tile=(4, 16, 16),
        photometric="minisblack",
        compression="zlib",
    ),
}


@pytest.fixture(params=sorted(LAYOUTS))
def tiff_path(request, tmp_path):
    path = str(tmp_path / f"{request.param}.tif")
    tifffile.imwrite(path, **LAYOUTS[request.param])
    return path


def full_read(path):
    image = tifffile.imread(path)
    return image[np.newaxis] if image.ndim == 2 else image


def reference_save_tiff2json(tiff_filepath, save_dirpath, start_index, end_index):
    """save_tiff2json before windowed reads: decode the stack, keep the window."""
    tiffs = []
    image = tifffile.imread(tiff_filepath)
    if image.ndim == 2:
        image = image[np.newaxis, :, :]
    for i, page in enumerate(image):
        if i < start_index - 1:
            continue
        if i >= end_index:
            break
        tiffs.append(page.tolist())

    filename, _ = os.path.splitext(os.path.basename(tiff_filepath))
    os.makedirs(save_dirpath, exist_ok=True)
    json_str = pd.DataFrame(pd.DataFrame(tiffs)).to_json(indent=4, orient="split")
    with open(
        os.path.join(save_dirpath, f"{filename}_{start_index}_{end_index}.json"), "w"
    ) as f:
        f.write(json_str)


@pytest.mark.parametrize(
    "start, stop", [(0, 10), (2, 3), (3, 3), (4, 2), (18, 25), (30, 40), (-1, 2)]
)
def test_a_window_equals_the_same_frames_of_a_full_read(tiff_path, start, stop):
    expected = full_read(tiff_path)[max(start, 0) : stop]

    frames = read_tiff_frames(tiff_path, start, stop)

    assert frames.shape == expected.shape
    assert frames.dtype == expected.dtype
    assert np.array_equal(frames, expected)


def test_a_packbits_stack_is_read_by_window():
    expected = tifffile.imread(PACKBITS_TIFF)

    for start, stop in [(0, 10), (995, 1005)]:
        assert np.array_equal(
            read_tiff_frames(PACKBITS_TIFF, start, stop), expected[start:stop]
        )


@pytest.mark.parametrize(
    "start_index, end_index", [(0, 10), (1, 10), (1, 1), (3, 5), (15, 40), (25, 30)]
)
def test_the_preview_json_is_byte_identical_to_a_full_read(
    tiff_path, tmp_path, start_index, end_index
):
    save_tiff2json(tiff_path, str(tmp_path / "new"), start_index, end_index)
    reference_save_tiff2json(tiff_path, str(tmp_path / "old"), start_index, end_index)

    name = os.path.splitext(os.path.basename(tiff_path))[0]
    json_name = f"{name}_{start_index}_{end_index}.json"
    new = (tmp_path / "new" / json_name).read_bytes()
    assert new == (tmp_path / "old" / json_name).read_bytes()


@pytest.mark.parametrize("compression", [None, "zlib"])
def test_a_window_does_not_decode_the_rest_of_the_stack(tmp_path, compression):
    stack = RNG.integers(0, 4000, (400, 128, 128), dtype=np.uint16)
    path = str(tmp_path / "stack.tif")
    tifffile.imwrite(path, stack, compression=compression)
    stack_bytes = stack.nbytes
    del stack

    tracemalloc.start()
    try:
        frames = read_tiff_frames(path, 0, 10)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert frames.shape == (10, 128, 128)
    assert peak < stack_bytes / 4


def test_a_window_over_the_pixel_budget_is_refused(tmp_path, monkeypatch):
    path = str(tmp_path / "stack.tif")
    tifffile.imwrite(path, STACK_3D)
    monkeypatch.setattr(json_writer, "TIFF_PREVIEW_MAX_PIXELS", 5 * 8 * 6)

    with pytest.raises(TiffPreviewTooLargeError) as excinfo:
        read_tiff_frames(path, 0, 6)

    assert str(excinfo.value) == (
        "Frames 1-6 are too many to preview at 8 x 6; choose at most 5 frames."
    )
    assert read_tiff_frames(path, 0, 5).shape == (5, 8, 6)


def test_a_frame_over_the_pixel_budget_is_refused(tmp_path, monkeypatch):
    path = str(tmp_path / "stack.tif")
    tifffile.imwrite(path, STACK_3D)
    monkeypatch.setattr(json_writer, "TIFF_PREVIEW_MAX_PIXELS", 8 * 6 - 1)

    with pytest.raises(TiffPreviewTooLargeError) as excinfo:
        read_tiff_frames(path, 0, 1)

    assert str(excinfo.value) == "A single 8 x 6 frame is too large to preview."


def test_a_layout_decoded_whole_is_refused_over_its_byte_budget(tmp_path, monkeypatch):
    path = str(tmp_path / "volume.tif")
    tifffile.imwrite(path, **LAYOUTS["volumetric"])
    monkeypatch.setattr(json_writer, "TIFF_PREVIEW_MAX_DECODE_BYTES", VOLUME.nbytes - 1)

    with pytest.raises(TiffPreviewTooLargeError, match="cannot be read frame by"):
        read_tiff_frames(path, 0, 1)


def test_a_frame_of_several_pages_is_read_without_a_whole_decode(tmp_path, monkeypatch):
    path = str(tmp_path / "hyperstack.tif")
    tifffile.imwrite(path, **LAYOUTS["compressed_hyperstack"])
    monkeypatch.setattr(json_writer, "TIFF_PREVIEW_MAX_DECODE_BYTES", 0)

    assert np.array_equal(read_tiff_frames(path, 1, 3), STACK_4D[1:3])


def test_the_preview_file_keeps_the_umask_mode(tmp_path):
    path = str(tmp_path / "stack.tif")
    tifffile.imwrite(path, STACK_3D)
    save_dirpath = tmp_path / "stack"
    save_tiff2json(path, str(save_dirpath), 1, 10)
    sibling = save_dirpath / "sibling"
    sibling.write_text("")

    preview = save_dirpath / "stack_1_10.json"
    assert preview.stat().st_mode == sibling.stat().st_mode


def test_a_refused_preview_writes_nothing(tmp_path, monkeypatch):
    path = str(tmp_path / "stack.tif")
    tifffile.imwrite(path, STACK_3D)
    monkeypatch.setattr(json_writer, "TIFF_PREVIEW_MAX_PIXELS", 8 * 6)
    save_dirpath = tmp_path / "stack"

    with pytest.raises(TiffPreviewTooLargeError):
        save_tiff2json(path, str(save_dirpath), 1, 10)

    assert not save_dirpath.exists()


def test_a_failed_write_leaves_no_partial_preview(tmp_path):
    path = str(tmp_path / "stack.tif")
    tifffile.imwrite(path, STACK_3D)
    save_dirpath = tmp_path / "stack"

    with patch.object(json_writer.os, "replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            save_tiff2json(path, str(save_dirpath), 1, 10)

    assert os.listdir(save_dirpath) == []
