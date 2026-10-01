"""Thumbnails — on-demand, disk-cached previews for the review UI.

Thumbnails are generated with OpenCV and cached under
``<folder>/_autocull_cache/`` so repeat views are instant. RAW files use
the fast embedded-preview path already implemented in the extractors.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import cv2

from autocull.extractors.preview import load_image
from autocull.web.state import CACHE_DIRNAME

DEFAULT_SIZE = 480
JPEG_QUALITY = 80


def _is_within(folder: Path, target: Path) -> bool:
    """Guard against path traversal: target must live inside folder."""
    try:
        target.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def get_thumbnail(folder: str, name: str, size: int = DEFAULT_SIZE) -> Optional[str]:
    """Return the path to a cached thumbnail for *name*, creating it if needed.

    Args:
        folder: Session folder (the photo lives inside it, maybe in a subfolder).
        name: Image path relative to *folder* (subfolders allowed).
        size: Longest-edge size in pixels.

    Returns:
        Absolute path to the thumbnail JPEG, or ``None`` if the source is
        missing / unreadable / outside the folder.
    """
    folder_path = Path(folder)
    # *name* may be a relative path (carretes de Sony vienen en 100MSDCF/, 101MSDCF/...);
    # _is_within is what keeps it from escaping the session folder.
    rel = Path(name.replace("\\", "/"))
    if rel.is_absolute() or ".." in rel.parts:
        return None

    src = folder_path / rel
    if not _is_within(folder_path, src) or not src.is_file():
        return None

    size = max(64, min(int(size), 2000))
    cache_dir = folder_path / CACHE_DIRNAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Flatten the relative path so subfolders can't collide in the cache dir.
    stem = "__".join(rel.with_suffix("").parts)
    thumb = cache_dir / f"{stem}_{size}.jpg"

    # Reuse cache if newer than the source.
    if thumb.is_file() and thumb.stat().st_mtime >= src.stat().st_mtime:
        return str(thumb)

    img = load_image(str(src))
    if img is None:
        return None

    h, w = img.shape[:2]
    scale = size / max(h, w)
    if scale < 1.0:
        img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                         interpolation=cv2.INTER_AREA)

    ok = cv2.imwrite(str(thumb), img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return str(thumb) if ok else None
