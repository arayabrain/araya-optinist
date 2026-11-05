"""
Build a benchmark input of another length from a real recording: its first
--frames frames (default: all), repeated --repeat times.

Streams page by page, so memory stays at one frame regardless of size. The
repeat is a caveat for ROI detection (content recurs), not for registration
memory, which scales with frame count. Record the recipe printed at the end
alongside any result that uses the file.

Usage (any python with tifffile, e.g. inside the benchmark image):
  python make_large_input.py <src.tif> <dst.tif> [--repeat 2] [--frames N]
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
    p.add_argument("--frames", type=int, help="use only the first N frames")
    a = p.parse_args()

    with tifffile.TiffFile(a.src) as src:
        n_src = len(src.pages)
        n_frames = min(a.frames, n_src) if a.frames else n_src
        first = src.pages[0]
        shape, dtype = first.shape, first.dtype
        with tifffile.TiffWriter(a.dst, bigtiff=True) as dst:
            for _ in range(a.repeat):
                for index in range(n_frames):
                    dst.write(src.pages[index].asarray(), contiguous=True)

    digest = hashlib.sha256()
    with open(a.dst, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    used = f"first {n_frames} of {n_src} frames" if n_frames < n_src else "frames"
    print(
        json.dumps(
            {
                "recipe": f"{os.path.basename(a.src)} {used} repeated x{a.repeat}",
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
