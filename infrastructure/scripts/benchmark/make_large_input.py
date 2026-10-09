"""
Build the upper-range benchmark input by repeating a real recording's frames.

Streams page by page, so memory stays at one frame regardless of size. The
repeat is a caveat for ROI detection (content recurs), not for registration
memory, which scales with frame count. Record the recipe printed at the end
alongside any result that uses the file.

Usage (any python with tifffile, e.g. inside the benchmark image):
  python make_large_input.py <src.tif> <dst.tif> [--repeat 2]
"""

import argparse
import hashlib
import json
import os

import tifffile


def main():
    p = argparse.ArgumentParser()
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("--repeat", type=int, default=2)
    a = p.parse_args()

    with tifffile.TiffFile(a.src) as src:
        n_frames = len(src.pages)
        first = src.pages[0]
        shape, dtype = first.shape, first.dtype
        with tifffile.TiffWriter(a.dst, bigtiff=True) as dst:
            for _ in range(a.repeat):
                for page in src.pages:
                    dst.write(page.asarray(), contiguous=True)

    digest = hashlib.sha256()
    with open(a.dst, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    print(
        json.dumps(
            {
                "recipe": f"{os.path.basename(a.src)} frames repeated x{a.repeat}",
                "frames": n_frames * a.repeat,
                "frame_shape": list(shape),
                "dtype": str(dtype),
                "bytes": os.path.getsize(a.dst),
                "sha256": digest.hexdigest(),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
