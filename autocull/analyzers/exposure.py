"""
Autocull — Exposure Analysis Module.

Evaluates exposure quality at two levels:

1. **Global** — Full-frame shadow / highlight ratio.  For night football,
   the pitch is typically well-lit but the background is very dark, so
   high shadow ratios are *normal*.  We only reject when >60% of pixels
   are in the near-black range [0–15], which indicates a severely
   underexposed frame (e.g. the exposure locked on a floodlight).

2. **Local (subject)** — Mean brightness inside the player's bounding box.
   Even if the overall frame is fine, a backlit player can be too dark
   to be usable.  We reject if the subject's mean luminance < 35.

Thresholds are taken from ``AnalysisConfig`` so they can be tuned per
session (e.g. indoor vs outdoor, different stadium lighting).
"""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
import cv2

from autocull.config import AnalysisConfig, DEFAULT_CONFIG


# ── Helpers ─────────────────────────────────────────────────────────────────

def _extract_roi(
    image: np.ndarray,
    bbox: Tuple[int, int, int, int],
) -> np.ndarray | None:
    """Safely extract a ROI, clamping to image bounds.

    Parameters
    ----------
    image : np.ndarray
        Source image (any number of channels).
    bbox : tuple
        ``(x1, y1, x2, y2)`` corner coordinates in pixels (matches the
        YOLO detectors and the rest of the pipeline).

    Returns
    -------
    np.ndarray or None
        Cropped region, or ``None`` if the ROI is empty.
    """
    img_h, img_w = image.shape[:2]
    x1, y1, x2, y2 = bbox

    x1 = max(0, int(x1))
    y1 = max(0, int(y1))
    x2 = min(img_w, int(x2))
    y2 = min(img_h, int(y2))

    if x2 <= x1 or y2 <= y1:
        return None

    return image[y1:y2, x1:x2]


# ── Global Exposure Analysis ───────────────────────────────────────────────

def analyze_exposure_global(
    gray: np.ndarray,
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> Dict[str, object]:
    """Analyse full-frame exposure.

    Computes the fraction of pixels in shadow and highlight ranges and
    flags the image if those fractions exceed the configured thresholds.

    Parameters
    ----------
    gray : np.ndarray
        Single-channel uint8 grayscale image (H×W).
    config : AnalysisConfig, optional
        Configuration providing pixel thresholds and ratio limits.

    Returns
    -------
    dict
        ``{
            'shadow_ratio': float,       # fraction of pixels ≤ underexposure_pixel_max
            'highlight_ratio': float,     # fraction of pixels ≥ overexposure_pixel_min
            'mean_brightness': float,     # global mean luminance [0-255]
            'severely_underexposed': bool,
            'blown_highlights': bool,
        }``
    """
    # ── Degenerate input guards ─────────────────────────────────────────
    if gray is None or gray.size == 0:
        return {
            "shadow_ratio": 1.0,
            "highlight_ratio": 0.0,
            "mean_brightness": 0.0,
            "severely_underexposed": True,
            "blown_highlights": False,
        }

    if gray.ndim != 2:
        raise ValueError(
            f"Expected 2-D grayscale image, got shape {gray.shape}"
        )

    total_pixels: int = gray.size

    # Shadow ratio: pixels in [0, underexposure_pixel_max]
    shadow_count: int = int(np.count_nonzero(gray <= config.underexposure_pixel_max))
    shadow_ratio: float = shadow_count / total_pixels

    # Highlight ratio: pixels in [overexposure_pixel_min, 255]
    highlight_count: int = int(np.count_nonzero(gray >= config.overexposure_pixel_min))
    highlight_ratio: float = highlight_count / total_pixels

    # Mean brightness
    mean_brightness: float = float(np.mean(gray))

    # Flags
    severely_underexposed: bool = shadow_ratio > config.underexposure_shadow_threshold
    blown_highlights: bool = highlight_ratio > config.overexposure_highlight_threshold

    return {
        "shadow_ratio": round(shadow_ratio, 4),
        "highlight_ratio": round(highlight_ratio, 4),
        "mean_brightness": round(mean_brightness, 2),
        "severely_underexposed": severely_underexposed,
        "blown_highlights": blown_highlights,
    }


# ── Local (Subject) Exposure Analysis ──────────────────────────────────────

def analyze_exposure_local(
    image_bgr: np.ndarray,
    bbox: Tuple[int, int, int, int],
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> Dict[str, object]:
    """Analyse exposure within a bounding box (typically the player).

    Measures mean brightness of the subject region and flags it if the
    player is too dark (backlit) or too bright (overexposed jersey).

    Parameters
    ----------
    image_bgr : np.ndarray
        BGR colour image (H×W×3) as loaded by ``cv2.imread``.
    bbox : tuple of int
        ``(x1, y1, x2, y2)`` corner bounding box in pixel coordinates
        (as returned by the YOLO detectors).
    config : AnalysisConfig, optional
        Configuration providing ``subject_min_brightness`` and
        ``subject_max_brightness``.

    Returns
    -------
    dict
        ``{
            'mean_brightness': float,    # mean luminance in [0-255]
            'subject_too_dark': bool,    # True if mean < subject_min_brightness
            'subject_too_bright': bool,  # True if mean ≥ subject_max_brightness
            'shadow_ratio': float,       # shadow ratio within ROI
            'highlight_ratio': float,    # highlight ratio within ROI
        }``

        Returns a worst-case dict for degenerate inputs (empty images,
        invalid bounding boxes).
    """
    _bad_result: Dict[str, object] = {
        "mean_brightness": 0.0,
        "subject_too_dark": True,
        "subject_too_bright": False,
        "shadow_ratio": 1.0,
        "highlight_ratio": 0.0,
    }

    if image_bgr is None or image_bgr.size == 0:
        return _bad_result

    # Convert to grayscale
    if image_bgr.ndim == 3:
        gray_full: np.ndarray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    elif image_bgr.ndim == 2:
        gray_full = image_bgr
    else:
        return _bad_result

    roi = _extract_roi(gray_full, bbox)
    if roi is None or roi.size == 0:
        return _bad_result

    # Mean brightness
    mean_brightness: float = float(np.mean(roi))

    total_pixels: int = roi.size

    # Shadow / highlight ratios within the ROI
    shadow_count: int = int(np.count_nonzero(roi <= config.underexposure_pixel_max))
    shadow_ratio: float = shadow_count / total_pixels

    highlight_count: int = int(np.count_nonzero(roi >= config.overexposure_pixel_min))
    highlight_ratio: float = highlight_count / total_pixels

    # Flags
    subject_too_dark: bool = mean_brightness < config.subject_min_brightness
    subject_too_bright: bool = mean_brightness >= config.subject_max_brightness

    return {
        "mean_brightness": round(mean_brightness, 2),
        "subject_too_dark": subject_too_dark,
        "subject_too_bright": subject_too_bright,
        "shadow_ratio": round(shadow_ratio, 4),
        "highlight_ratio": round(highlight_ratio, 4),
    }


# ── Self-test ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("exposure.py — self-test")
    print("=" * 60)

    rng = np.random.default_rng(42)

    # 1. Well-exposed image (mean ≈ 128)
    well_exposed = np.clip(
        rng.normal(128, 30, (400, 400)), 0, 255
    ).astype(np.uint8)
    result = analyze_exposure_global(well_exposed)
    print(
        f"[well-exposed]   shadow={result['shadow_ratio']:.3f}  "
        f"highlight={result['highlight_ratio']:.3f}  "
        f"mean={result['mean_brightness']:.1f}  "
        f"underexp={result['severely_underexposed']}  "
        f"blown={result['blown_highlights']}"
    )

    # 2. Severely underexposed (mostly black)
    dark = np.clip(rng.normal(8, 4, (400, 400)), 0, 255).astype(np.uint8)
    result_dark = analyze_exposure_global(dark)
    print(
        f"[severely dark]  shadow={result_dark['shadow_ratio']:.3f}  "
        f"mean={result_dark['mean_brightness']:.1f}  "
        f"underexp={result_dark['severely_underexposed']}"
    )
    assert result_dark["severely_underexposed"], "Should flag as underexposed!"

    # 3. Blown highlights (mostly white)
    bright = np.clip(rng.normal(250, 3, (400, 400)), 0, 255).astype(np.uint8)
    result_bright = analyze_exposure_global(bright)
    print(
        f"[blown highl]    highlight={result_bright['highlight_ratio']:.3f}  "
        f"mean={result_bright['mean_brightness']:.1f}  "
        f"blown={result_bright['blown_highlights']}"
    )
    assert result_bright["blown_highlights"], "Should flag as blown highlights!"

    # 4. All-black frame
    black = np.zeros((200, 200), dtype=np.uint8)
    result_black = analyze_exposure_global(black)
    print(
        f"[all-black]      shadow={result_black['shadow_ratio']:.3f}  "
        f"underexp={result_black['severely_underexposed']}"
    )

    # 5. Local exposure — bright subject on dark background
    dark_bg = np.full((400, 400, 3), 10, dtype=np.uint8)
    # Bright player region
    dark_bg[100:300, 150:250, :] = 140
    bbox = (150, 100, 250, 300)  # (x1, y1, x2, y2)
    result_local = analyze_exposure_local(dark_bg, bbox)
    print(
        f"[bright subject] mean={result_local['mean_brightness']:.1f}  "
        f"too_dark={result_local['subject_too_dark']}  "
        f"too_bright={result_local['subject_too_bright']}"
    )
    assert not result_local["subject_too_dark"], "Bright subject should not be 'too dark'"

    # 6. Local exposure — dark subject (backlit)
    bright_bg = np.full((400, 400, 3), 180, dtype=np.uint8)
    bright_bg[100:300, 150:250, :] = 20  # Dark player
    result_local_dark = analyze_exposure_local(bright_bg, bbox)
    print(
        f"[dark subject]   mean={result_local_dark['mean_brightness']:.1f}  "
        f"too_dark={result_local_dark['subject_too_dark']}"
    )
    assert result_local_dark["subject_too_dark"], "Dark subject should be flagged!"

    # 7. Invalid bbox (x2 <= x1)
    result_invalid = analyze_exposure_local(dark_bg, (-50, -50, -10, -10))
    print(
        f"[invalid bbox]   mean={result_invalid['mean_brightness']:.1f}  "
        f"too_dark={result_invalid['subject_too_dark']}"
    )

    # 8. Empty image
    empty = np.array([], dtype=np.uint8).reshape(0, 0)
    result_empty = analyze_exposure_global(empty)
    print(f"[empty image]    underexp={result_empty['severely_underexposed']}")

    print("\n✓ All tests passed.")
