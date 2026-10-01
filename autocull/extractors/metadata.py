"""
EXIF Metadata Reader — Extract camera settings from image files.

Reads ISO, shutter speed, aperture, focal length, and timestamps
using exifread (pure Python, no external dependencies).

ISO is critical for the adaptive bilateral filter (noise-aware pipeline).
Timestamps are used for session grouping.
"""

import os
from typing import Optional
from datetime import datetime


def read_exif(file_path: str) -> dict:
    """Read EXIF metadata from an image file.

    Uses exifread for pure-Python, dependency-free EXIF reading.
    Works with RAW files (ARW, CR2, NEF) and JPEGs.

    Args:
        file_path: Path to the image file.

    Returns:
        Dictionary with standardized metadata fields:
        - iso: int (e.g., 6400)
        - shutter_speed: str (e.g., "1/500")
        - aperture: float (e.g., 1.8)
        - focal_length: float (e.g., 85.0)
        - camera_model: str (e.g., "ILCE-7RM3")
        - datetime: str (e.g., "2024:11:15 21:30:45")
        - width: int
        - height: int
    """
    result = {
        "iso": 0,
        "shutter_speed": "",
        "shutter_speed_value": 0.0,
        "aperture": 0.0,
        "focal_length": 0.0,
        "camera_model": "",
        "datetime": "",
        "datetime_obj": None,
        "width": 0,
        "height": 0,
    }

    try:
        import exifread
    except ImportError:
        # Fallback: return defaults if exifread not available
        return result

    try:
        with open(file_path, "rb") as f:
            tags = exifread.process_file(f, details=False, stop_tag="EXIF DateTimeOriginal")
    except Exception:
        # Re-try with full read if stop_tag fails
        try:
            with open(file_path, "rb") as f:
                tags = exifread.process_file(f, details=False)
        except Exception:
            return result

    # ISO
    iso_tag = tags.get("EXIF ISOSpeedRatings")
    if iso_tag:
        try:
            result["iso"] = int(str(iso_tag))
        except (ValueError, TypeError):
            pass

    # Shutter speed
    exposure_tag = tags.get("EXIF ExposureTime")
    if exposure_tag:
        result["shutter_speed"] = str(exposure_tag)
        try:
            val = exposure_tag.values[0]
            if hasattr(val, 'num') and hasattr(val, 'den'):
                if val.den != 0:
                    result["shutter_speed_value"] = val.num / val.den
            else:
                result["shutter_speed_value"] = float(str(exposure_tag))
        except (IndexError, ValueError, TypeError, ZeroDivisionError):
            pass

    # Aperture
    aperture_tag = tags.get("EXIF FNumber")
    if aperture_tag:
        try:
            val = aperture_tag.values[0]
            if hasattr(val, 'num') and hasattr(val, 'den'):
                if val.den != 0:
                    result["aperture"] = round(val.num / val.den, 1)
            else:
                result["aperture"] = float(str(aperture_tag))
        except (IndexError, ValueError, TypeError, ZeroDivisionError):
            pass

    # Focal length
    fl_tag = tags.get("EXIF FocalLength")
    if fl_tag:
        try:
            val = fl_tag.values[0]
            if hasattr(val, 'num') and hasattr(val, 'den'):
                if val.den != 0:
                    result["focal_length"] = round(val.num / val.den, 1)
            else:
                result["focal_length"] = float(str(fl_tag))
        except (IndexError, ValueError, TypeError, ZeroDivisionError):
            pass

    # Camera model
    model_tag = tags.get("Image Model")
    if model_tag:
        result["camera_model"] = str(model_tag).strip()

    # DateTime
    dt_tag = tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime")
    if dt_tag:
        dt_str = str(dt_tag).strip()
        result["datetime"] = dt_str
        try:
            result["datetime_obj"] = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
        except ValueError:
            pass

    # Image dimensions
    width_tag = tags.get("EXIF ExifImageWidth") or tags.get("Image ImageWidth")
    height_tag = tags.get("EXIF ExifImageLength") or tags.get("Image ImageLength")
    if width_tag:
        try:
            result["width"] = int(str(width_tag))
        except (ValueError, TypeError):
            pass
    if height_tag:
        try:
            result["height"] = int(str(height_tag))
        except (ValueError, TypeError):
            pass

    return result


def read_exif_batch(file_paths: list[str]) -> list[dict]:
    """Read EXIF metadata from multiple files.

    Args:
        file_paths: List of image file paths.

    Returns:
        List of metadata dictionaries (same order as input).
    """
    return [read_exif(fp) for fp in file_paths]


def get_iso_or_default(exif_data: dict, default_iso: int = 3200) -> int:
    """Get ISO from EXIF data, with a sensible default for night sports.

    If EXIF reading fails or ISO is 0, assumes a reasonable night sports ISO.

    Args:
        exif_data: Dictionary from read_exif().
        default_iso: Default ISO if not found (default: 3200 for night sports).

    Returns:
        ISO value as integer.
    """
    iso = exif_data.get("iso", 0)
    return iso if iso > 0 else default_iso


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        exif = read_exif(sys.argv[1])
        for key, value in exif.items():
            if key != "datetime_obj":
                print(f"  {key}: {value}")
    else:
        print("Usage: python -m autocull.extractors.metadata <image_path>")
