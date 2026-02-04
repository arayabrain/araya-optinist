"""Thumbnail generation utilities for DataView."""

import json

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
        cls, roi_json_path: str, output_path: str, max_size: int = 512
    ) -> None:
        """
        Generate a PNG thumbnail from ROI data (cell_roi.json).

        Creates a colored image showing ROI masks where each ROI gets a distinct color.

        The cell_roi.json format is a pandas DataFrame in "split" orientation:
        {"columns": [...], "index": [...], "data": [[[...pixel values...]]]}

        The data contains a 2D mask where pixel values indicate ROI membership
        (0 = background, 1+ = ROI index).

        Args:
            roi_json_path: Path to cell_roi.json file
            output_path: Path to save PNG thumbnail
            max_size: Maximum dimension for thumbnail (default 512px)
        """
        with open(roi_json_path) as f:
            roi_data = json.load(f)

        # Extract the mask array from pandas split format
        # Format: {"columns": [...], "index": [...], "data": [[[row0], [row1], ...]]}
        if "data" not in roi_data:
            # Fallback: white image (indicates no data)
            img = np.full((max_size, max_size, 3), 255, dtype=np.uint8)
            imageio.imwrite(output_path, img)
            return

        # The data is nested: outer list is rows of the DataFrame,
        # each row contains the image data
        data = roi_data["data"]
        if not data or not data[0]:
            img = np.full((max_size, max_size, 3), 255, dtype=np.uint8)
            imageio.imwrite(output_path, img)
            return

        # Convert to numpy array - data[0] is the first row containing the 2D mask
        mask = np.array(data[0], dtype=np.float32)

        # Handle case where mask might be 1D (flattened) or have extra dimensions
        if mask.ndim == 1:
            # Try to infer square shape
            side = int(np.sqrt(len(mask)))
            if side * side == len(mask):
                mask = mask.reshape(side, side)
            else:
                img = np.full((max_size, max_size, 3), 255, dtype=np.uint8)
                imageio.imwrite(output_path, img)
                return

        # Get unique ROI values (excluding 0 which is background)
        unique_vals = np.unique(mask)
        unique_vals = unique_vals[unique_vals > 0]

        if len(unique_vals) == 0:
            # No ROIs found, save white image
            img = np.full((max_size, max_size, 3), 255, dtype=np.uint8)
            imageio.imwrite(output_path, img)
            return

        # Create RGB image with white background
        h, w = mask.shape
        img = np.full((h, w, 3), 255, dtype=np.uint8)

        # Generate distinct colors for each ROI using HSV color space
        np.random.seed(42)  # Consistent colors across runs
        n_rois = len(unique_vals)

        # Use golden ratio for hue distribution to get well-separated colors
        hues = np.linspace(0, 1, n_rois, endpoint=False)
        np.random.shuffle(hues)

        for i, val in enumerate(unique_vals):
            # Convert HSV to RGB (H: 0-1, S: 0.7-1.0, V: 0.7-1.0)
            hue = hues[i % len(hues)]
            saturation = 0.8 + np.random.random() * 0.2
            value = 0.7 + np.random.random() * 0.3

            # HSV to RGB conversion
            c = value * saturation
            x = c * (1 - abs((hue * 6) % 2 - 1))
            m = value - c

            if hue < 1 / 6:
                r, g, b = c, x, 0
            elif hue < 2 / 6:
                r, g, b = x, c, 0
            elif hue < 3 / 6:
                r, g, b = 0, c, x
            elif hue < 4 / 6:
                r, g, b = 0, x, c
            elif hue < 5 / 6:
                r, g, b = x, 0, c
            else:
                r, g, b = c, 0, x

            color = (
                int((r + m) * 255),
                int((g + m) * 255),
                int((b + m) * 255),
            )

            # Fill ROI pixels with this color
            roi_mask = mask == val
            img[roi_mask] = color

        # Resize if larger than max_size while preserving aspect ratio
        if max(h, w) > max_size:
            scale = max_size / max(h, w)
            new_h, new_w = int(h * scale), int(w * scale)
            # Simple resize using slicing (nearest neighbor)
            y_indices = (np.arange(new_h) * h / new_h).astype(int)
            x_indices = (np.arange(new_w) * w / new_w).astype(int)
            img = img[np.ix_(y_indices, x_indices)]

        imageio.imwrite(output_path, img)
