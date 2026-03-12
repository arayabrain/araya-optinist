"""Thumbnail generation utilities for DataView."""

import json
import os
from typing import Optional

import imageio.v3 as imageio
import numpy as np
import tifffile

from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.const import EXTENSION_LABELS, ThumbnailConst, ThumbnailType
from studio.app.dir_path import DIRPATH


class ThumbnailGenerator:
    """Utility class for generating PNG thumbnails from various sources."""

    @classmethod
    def get_thumbnail_path(
        cls, workspace_id: str, unique_id: str, thumb_type: ThumbnailType
    ) -> str:
        """
        Get the absolute path for a thumbnail PNG.

        Args:
            workspace_id: Workspace identifier
            unique_id: Experiment unique identifier
            thumb_type: ThumbnailType.INPUT or ThumbnailType.ROI

        Returns:
            Absolute path to the thumbnail PNG file
        """
        return join_filepath(
            [
                DIRPATH.OUTPUT_DIR,
                workspace_id,
                unique_id,
                ThumbnailConst.DIRNAME,
                thumb_type.filename,
            ]
        )

    @classmethod
    def resolve_source_path(cls, workspace_id: str, file_path: str) -> Optional[str]:
        """
        Resolve a source file path to an absolute path.

        Tries in order:
        1. Already absolute and exists → return as-is
        2. Relative from OUTPUT_DIR → return if exists
        3. Basename in INPUT_DIR/workspace_id → return if exists
        4. None if not found

        Args:
            workspace_id: Workspace identifier
            file_path: File path (absolute, relative, or just filename)

        Returns:
            Absolute path if found, None otherwise
        """
        if os.path.isabs(file_path) and os.path.exists(file_path):
            return file_path

        # Try as relative path from output dir
        abs_path = join_filepath([DIRPATH.OUTPUT_DIR, file_path])
        if os.path.exists(abs_path):
            return abs_path

        # Try as input file (just filename)
        filename = os.path.basename(file_path)
        input_path = join_filepath([DIRPATH.INPUT_DIR, workspace_id, filename])
        if os.path.exists(input_path):
            return input_path

        return None

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

    @classmethod
    def generate_placeholder_thumbnail(
        cls,
        output_path: str,
        file_path: str = None,
        label: str = None,
        size: int = 256,
    ) -> None:
        """
        Generate a placeholder PNG thumbnail with text label.

        Used for input file types that cannot be rendered as images (HDF5, MAT,
        microscope formats, etc.). Creates a light gray background with the
        file type label centered.

        Args:
            output_path: Path to save PNG thumbnail
            file_path: Optional source file path (used to detect type from extension)
            label: Optional explicit label text (overrides file_path detection)
            size: Thumbnail size in pixels (default 256px)
        """
        # Determine label from file extension if not provided
        if label is None and file_path:
            label = cls.get_file_type_label(file_path)

        if label is None:
            label = "DATA"

        # Create white background
        img = np.full((size, size, 3), 255, dtype=np.uint8)

        # Draw text using simple pixel-based rendering
        # This avoids PIL/font dependencies
        cls._draw_text_on_image(img, label)

        imageio.imwrite(output_path, img)

    @classmethod
    def _draw_text_on_image(cls, img: np.ndarray, text: str) -> None:
        """
        Draw centered text on an image using simple pixel rendering.

        This is a basic implementation that doesn't require PIL or font files.
        Uses a simple 5x7 pixel font for ASCII characters.
        """
        # Simple 5x7 bitmap font for uppercase letters and numbers
        # Each character is represented as a list of 7 rows, each row is 5 bits
        font = {
            "A": [0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
            "B": [0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E],
            "C": [0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E],
            "D": [0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E],
            "E": [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F],
            "F": [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10],
            "G": [0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0E],
            "H": [0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
            "I": [0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
            "J": [0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C],
            "K": [0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11],
            "L": [0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F],
            "M": [0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11],
            "N": [0x11, 0x11, 0x19, 0x15, 0x13, 0x11, 0x11],
            "O": [0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
            "P": [0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10],
            "Q": [0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D],
            "R": [0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11],
            "S": [0x0E, 0x11, 0x10, 0x0E, 0x01, 0x11, 0x0E],
            "T": [0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
            "U": [0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
            "V": [0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04],
            "W": [0x11, 0x11, 0x11, 0x15, 0x15, 0x15, 0x0A],
            "X": [0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11],
            "Y": [0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04],
            "Z": [0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F],
            "0": [0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E],
            "1": [0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E],
            "2": [0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F],
            "3": [0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E],
            "4": [0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02],
            "5": [0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E],
            "6": [0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E],
            "7": [0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08],
            "8": [0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E],
            "9": [0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C],
            " ": [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
            "-": [0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00],
            ".": [0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C],
        }

        h, w = img.shape[:2]
        char_width = 5
        char_height = 7
        scale = max(1, min(h, w) // 64)  # Scale based on image size
        spacing = 1

        # Calculate text dimensions
        text_upper = text.upper()
        text_width = len(text_upper) * (char_width + spacing) * scale - spacing * scale
        text_height = char_height * scale

        # Center position
        start_x = (w - text_width) // 2
        start_y = (h - text_height) // 2

        # Dark gray color for text (RGB: 100, 100, 100)
        text_color = [100, 100, 100]

        # Draw each character
        for char_idx, char in enumerate(text_upper):
            if char not in font:
                continue
            char_data = font[char]
            char_x = start_x + char_idx * (char_width + spacing) * scale

            for row_idx, row_bits in enumerate(char_data):
                for col_idx in range(char_width):
                    if row_bits & (0x10 >> col_idx):  # Check if bit is set
                        # Draw scaled pixel
                        px = char_x + col_idx * scale
                        py = start_y + row_idx * scale
                        for dy in range(scale):
                            for dx in range(scale):
                                if 0 <= py + dy < h and 0 <= px + dx < w:
                                    img[py + dy, px + dx] = text_color

    @classmethod
    def generate_input_thumbnail(
        cls, source_path: str, output_path: str, abs_source_path: str = None
    ) -> None:
        """
        Generate an input thumbnail, choosing the right strategy automatically.

        - TIFF file exists locally → render first frame as grayscale
        - TIFF file not found locally → placeholder with file type label
        - Non-TIFF file (HDF5, MAT, etc.) → placeholder with file type label

        Args:
            source_path: Original file path (used for extension detection and label)
            output_path: Path to save the PNG thumbnail
            abs_source_path: Absolute path to the source file for reading.
                If None, uses source_path directly.
        """
        resolved = abs_source_path or source_path

        if cls.can_generate_tiff_thumbnail(source_path):
            if resolved and os.path.exists(resolved):
                cls.generate_tiff_thumbnail(resolved, output_path)
            else:
                cls.generate_placeholder_thumbnail(output_path, file_path=source_path)
        else:
            cls.generate_placeholder_thumbnail(output_path, file_path=source_path)

    @classmethod
    def can_generate_tiff_thumbnail(cls, file_path: str) -> bool:
        """
        Check if a file can have a TIFF-based thumbnail generated.

        Args:
            file_path: Path to the file

        Returns:
            True if file is a TIFF that can be rendered as thumbnail
        """
        if not file_path:
            return False
        ext = os.path.splitext(file_path)[1].lower()
        return ext in (".tif", ".tiff")

    @classmethod
    def get_file_type_label(cls, file_path: str) -> str:
        """
        Get the display label for a file based on its extension.

        Args:
            file_path: Path to the file

        Returns:
            Human-readable label for the file type
        """
        if not file_path:
            return "DATA"

        file_lower = file_path.lower()
        for ext, label in EXTENSION_LABELS.items():
            if file_lower.endswith(ext):
                return label

        _, ext = os.path.splitext(file_path)
        return ext.upper().lstrip(".") if ext else "DATA"
