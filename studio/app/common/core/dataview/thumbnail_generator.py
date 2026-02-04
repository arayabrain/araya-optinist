"""Thumbnail generation utilities for DataView."""

import json
from typing import Tuple

import imageio.v3 as imageio
import numpy as np
import tifffile


class ThumbnailGenerator:
    """Utility class for generating PNG thumbnails from various sources."""

    @classmethod
    def generate_tiff_thumbnail(
        cls, tiff_path: str, output_path: str, max_size: int = 512
    ) -> None:
        """
        Generate a PNG thumbnail from the first frame of a TIFF file.

        Args:
            tiff_path: Path to source TIFF file
            output_path: Path to save PNG thumbnail
            max_size: Maximum dimension for thumbnail (default 512px)
        """
        # Read only the first frame to minimize memory usage
        img = tifffile.imread(tiff_path, key=0)

        # Handle multi-channel images (take first channel or average)
        if img.ndim > 2:
            img = img[..., 0] if img.shape[-1] <= 4 else img[0]

        # Normalize to uint8
        img_float = img.astype(np.float32)
        img_min, img_max = img_float.min(), img_float.max()
        if img_max > img_min:
            img_normalized = ((img_float - img_min) / (img_max - img_min) * 255).astype(
                np.uint8
            )
        else:
            # Uniform image: set to mid-gray (128) for visibility
            img_normalized = np.full_like(img, 128, dtype=np.uint8)

        # Resize if larger than max_size while preserving aspect ratio
        h, w = img_normalized.shape[:2]
        if max(h, w) > max_size:
            scale = max_size / max(h, w)
            new_h, new_w = int(h * scale), int(w * scale)
            # Simple resize using slicing (nearest neighbor)
            y_indices = (np.arange(new_h) * h / new_h).astype(int)
            x_indices = (np.arange(new_w) * w / new_w).astype(int)
            img_normalized = img_normalized[np.ix_(y_indices, x_indices)]

        # Save as PNG
        imageio.imwrite(output_path, img_normalized)

    @classmethod
    def generate_roi_thumbnail(
        cls, roi_json_path: str, output_path: str, size: Tuple[int, int] = (512, 512)
    ) -> None:
        """
        Generate a PNG thumbnail from ROI data (cell_roi.json).

        Creates a colored image showing ROI outlines/masks.

        Args:
            roi_json_path: Path to cell_roi.json file
            output_path: Path to save PNG thumbnail
            size: Output image size (width, height)
        """
        with open(roi_json_path) as f:
            roi_data = json.load(f)

        # Initialize blank image (RGB)
        img = np.zeros((size[1], size[0], 3), dtype=np.uint8)

        # Get all ROIs and determine bounding box
        all_x = []
        all_y = []
        rois = []

        for key, value in roi_data.items():
            if isinstance(value, dict) and "x" in value and "y" in value:
                x_coords = value["x"]
                y_coords = value["y"]
                if x_coords and y_coords:
                    all_x.extend(x_coords)
                    all_y.extend(y_coords)
                    rois.append((x_coords, y_coords))

        if not rois:
            # No ROIs found, save blank image
            imageio.imwrite(output_path, img)
            return

        # Calculate scaling to fit ROIs in output image
        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)
        roi_width = max_x - min_x
        roi_height = max_y - min_y

        if roi_width == 0 or roi_height == 0:
            imageio.imwrite(output_path, img)
            return

        # Add padding (10%)
        padding = 0.1
        scale_x = size[0] * (1 - 2 * padding) / roi_width
        scale_y = size[1] * (1 - 2 * padding) / roi_height
        scale = min(scale_x, scale_y)

        offset_x = size[0] * padding - min_x * scale
        offset_y = size[1] * padding - min_y * scale

        # Generate colors for each ROI
        np.random.seed(42)  # Consistent colors
        colors = np.random.randint(100, 255, size=(len(rois), 3), dtype=np.uint8)

        # Draw each ROI
        for idx, (x_coords, y_coords) in enumerate(rois):
            color = tuple(int(c) for c in colors[idx])
            # Scale and offset coordinates
            scaled_x = [int(x * scale + offset_x) for x in x_coords]
            scaled_y = [int(y * scale + offset_y) for y in y_coords]

            # Draw polygon outline
            for i in range(len(scaled_x)):
                x1, y1 = scaled_x[i], scaled_y[i]
                x2, y2 = (
                    scaled_x[(i + 1) % len(scaled_x)],
                    scaled_y[(i + 1) % len(scaled_y)],
                )
                cls._draw_line(img, x1, y1, x2, y2, color)

        imageio.imwrite(output_path, img)

    @staticmethod
    def _draw_line(
        img: np.ndarray,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        color: Tuple[int, int, int],
    ) -> None:
        """Draw a line on an image using Bresenham's algorithm."""
        h, w = img.shape[:2]

        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        sx = 1 if x1 < x2 else -1
        sy = 1 if y1 < y2 else -1
        err = dx - dy

        while True:
            if 0 <= x1 < w and 0 <= y1 < h:
                img[y1, x1] = color

            if x1 == x2 and y1 == y2:
                break

            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x1 += sx
            if e2 < dx:
                err += dx
                y1 += sy
