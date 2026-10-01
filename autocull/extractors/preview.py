"""
Preview Extractor — Load images for analysis.

Supports two workflows:
1. JPEG mode: Load pre-exported JPEGs directly (fastest, no rawpy needed)
2. RAW mode: Extract embedded JPEG preview from ARW/CR2/NEF files via rawpy

For the user's workflow (Sony A7R III → export to JPEG 3000px 65%), JPEG mode
skips the rawpy dependency entirely.
"""

import os
import glob
from typing import Optional
from pathlib import Path

import cv2
import numpy as np

from autocull.config import AnalysisConfig, DEFAULT_CONFIG


# Autocull-generated subfolders that find_images must never re-ingest as input
# (cached thumbnails live in _autocull_cache/*.jpg, selections in _selected/).
_EXCLUDED_DIRS = {"_selected", "_rejected", "_autocull_cache"}


def _is_generated(path: str, input_dir: str, config: AnalysisConfig) -> bool:
    """True if *path* sits inside an Autocull-generated subfolder."""
    excluded = _EXCLUDED_DIRS | {config.accepted_folder}
    try:
        parts = Path(path).resolve().relative_to(Path(input_dir).resolve()).parts
    except ValueError:
        return False
    return any(part in excluded for part in parts[:-1])  # dirs only, not the file


def find_images(input_dir: str, config: AnalysisConfig = DEFAULT_CONFIG) -> tuple[list[str], str]:
    """Scan a directory for supported image files.

    Auto-detects whether the folder contains JPEGs or RAW files.
    If both exist, prefers JPEGs (faster analysis, no rawpy needed).

    Args:
        input_dir: Path to the directory to scan.
        config: Analysis configuration.

    Returns:
        Tuple of (list of file paths, format string "jpeg" or "raw").

    Raises:
        FileNotFoundError: If the directory doesn't exist.
        ValueError: If no supported images are found.
    """
    input_dir = os.path.abspath(input_dir)
    if not os.path.isdir(input_dir):
        raise FileNotFoundError(f"Directory not found: {input_dir}")

    # Scan for JPEGs first (preferred)
    jpeg_files = []
    for ext in config.jpeg_extensions:
        jpeg_files.extend(glob.glob(os.path.join(input_dir, f"**/*{ext}"), recursive=True))
        jpeg_files.extend(glob.glob(os.path.join(input_dir, f"**/*{ext.upper()}"), recursive=True))
    jpeg_files = sorted(p for p in set(jpeg_files) if not _is_generated(p, input_dir, config))

    # Scan for RAW files
    raw_files = []
    for ext in config.raw_extensions:
        raw_files.extend(glob.glob(os.path.join(input_dir, f"**/*{ext}"), recursive=True))
        raw_files.extend(glob.glob(os.path.join(input_dir, f"**/*{ext.upper()}"), recursive=True))
    raw_files = sorted(p for p in set(raw_files) if not _is_generated(p, input_dir, config))

    if jpeg_files:
        return jpeg_files, "jpeg"
    elif raw_files:
        return raw_files, "raw"
    else:
        raise ValueError(
            f"No supported images found in {input_dir}. "
            f"Supported: {config.jpeg_extensions + config.raw_extensions}"
        )


def load_image(file_path: str, config: AnalysisConfig = DEFAULT_CONFIG) -> Optional[np.ndarray]:
    """Load a single image for analysis.

    For JPEGs: loads directly with OpenCV.
    For RAW: extracts the embedded JPEG preview (fast, ~10ms).

    Args:
        file_path: Path to the image file.
        config: Analysis configuration.

    Returns:
        BGR numpy array, or None if loading fails.
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext in config.jpeg_extensions:
        return _load_jpeg(file_path)
    elif ext in config.raw_extensions:
        return _load_raw_preview(file_path)
    else:
        return None


def _load_jpeg(file_path: str) -> Optional[np.ndarray]:
    """Load a JPEG image with OpenCV.

    Args:
        file_path: Path to the JPEG file.

    Returns:
        BGR numpy array, or None if loading fails.
    """
    try:
        img = cv2.imread(file_path, cv2.IMREAD_COLOR)
        if img is None:
            return None
        return img
    except Exception:
        return None


def _load_raw_preview(file_path: str) -> Optional[np.ndarray]:
    """Extract the embedded JPEG preview from a RAW file.

    Most modern cameras (Sony, Canon, Nikon) embed a full-resolution or
    near-full-resolution JPEG preview inside the RAW file. Extracting this
    is near-instant (~10ms) vs full RAW decode (~5 seconds for 42MP).

    Args:
        file_path: Path to the RAW file.

    Returns:
        BGR numpy array, or None if extraction fails.
    """
    try:
        import rawpy
    except ImportError:
        raise ImportError(
            "rawpy is required for RAW file support. "
            "Install it with: pip install rawpy\n"
            "Or use pre-exported JPEGs instead (recommended for speed)."
        )

    try:
        with rawpy.imread(file_path) as raw:
            thumb = raw.extract_thumb()

            if thumb.format is not None and hasattr(thumb, 'data'):
                import rawpy
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    # Decode JPEG bytes to numpy array
                    nparr = np.frombuffer(thumb.data, np.uint8)
                    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    return img
                elif thumb.format == rawpy.ThumbFormat.BITMAP:
                    # Already a numpy RGB array — convert to BGR
                    return cv2.cvtColor(thumb.data, cv2.COLOR_RGB2BGR)
            return None
    except Exception:
        return None


def downsample_for_analysis(
    image: np.ndarray,
    target_megapixels: float = 2.0
) -> np.ndarray:
    """Downsample an image to the target resolution for analysis.

    Uses INTER_AREA interpolation (best quality for downsampling).
    If the image is already at or below target size, returns as-is.

    Args:
        image: BGR numpy array.
        target_megapixels: Target size in megapixels (default 2.0 = ~1600x1200).

    Returns:
        Downsampled BGR numpy array.
    """
    h, w = image.shape[:2]
    current_mp = (h * w) / 1_000_000

    if current_mp <= target_megapixels:
        return image

    scale = (target_megapixels / current_mp) ** 0.5
    new_w = int(w * scale)
    new_h = int(h * scale)

    return cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        test_dir = sys.argv[1]
        files, fmt = find_images(test_dir)
        print(f"Found {len(files)} {fmt} files")
        if files:
            img = load_image(files[0])
            if img is not None:
                print(f"First image: {files[0]} → {img.shape}")
                small = downsample_for_analysis(img)
                print(f"Downsampled: {small.shape}")
    else:
        print("Usage: python -m autocull.extractors.preview <directory>")
