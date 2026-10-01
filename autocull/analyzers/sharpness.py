"""
Autocull — Noise-Aware Sharpness Detection.

**Problem**: Standard Laplacian variance is *useless* at high ISO — sensor
grain creates uniform high-frequency energy that inflates the "sharpness"
score.  An ISO-10000 blurry photo can score *higher* than an ISO-800 sharp
one.

**Solution — 3-step noise-aware pipeline**:

1. **Adaptive bilateral denoise** — edge-preserving smoothing whose
   strength scales with ISO (via ``config.get_bilateral_params``).
   Removes grain while keeping real edges.

2. **P95 gradient magnitude** — Compute Sobel gradient magnitude on the
   denoised image and take the 95th percentile.  P95 captures the
   *strong* gradients (real edges) while the remaining 5% of outlier
   values (specular highlights, sensor defects) are discarded.

3. **SNR sharpness = P95 / σ(ISO)** — Normalise by the noise level
   *predicted from the ISO* (``config.expected_sigma``), which makes the
   metric comparable across ISOs.

**Why σ is predicted, not measured** (this used to be a real bug): estimating
σ from the pixels is circular for a sharpness metric.  Blur destroys the
high-frequency content that the MAD-of-Laplacian estimator reads, so a
motion-blurred frame reports a tiny σ, this divisor collapses (it used to hit
the ``min_sigma_noise`` floor), and **blurry photos outscored sharp ones** — a
blurred checkerboard scored 1334 vs 580 for the sharp original.  The sensor's
noise at a given ISO doesn't care whether the shot came out sharp, so ISO is
the honest source.  Bonus: a subject ROI and its frame share an ISO, hence one
σ, so a small/distant player is no longer normalised by a noisy per-crop
estimate.

Supports both *global* (full-frame) and *local* (bounding-box on the
player) evaluation.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np
import cv2

from autocull.config import AnalysisConfig, DEFAULT_CONFIG
from autocull.analyzers.noise import estimate_noise


# ── Adaptive Bilateral Denoise ──────────────────────────────────────────────

def adaptive_bilateral(
    gray: np.ndarray,
    iso: int,
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> np.ndarray:
    """Edge-preserving denoise with ISO-adaptive bilateral filter.

    The bilateral filter smooths flat regions (removing grain) while
    preserving edges.  The filter parameters (diameter, sigmaColor,
    sigmaSpace) are looked up from ``config.bilateral_params`` based on the
    camera ISO at capture time.

    Parameters
    ----------
    gray : np.ndarray
        Single-channel uint8 grayscale image (H×W).
    iso : int
        ISO value at capture time (e.g. 3200, 6400, 10000).
    config : AnalysisConfig, optional
        Configuration with bilateral parameter table.

    Returns
    -------
    np.ndarray
        Denoised uint8 grayscale image, same shape as input.

    Raises
    ------
    ValueError
        If *gray* is not 2-D.
    """
    if gray is None or gray.size == 0:
        return gray

    if gray.ndim != 2:
        raise ValueError(
            f"Expected 2-D grayscale image, got shape {gray.shape}"
        )

    d, sigma_color, sigma_space = config.get_bilateral_params(iso)
    denoised: np.ndarray = cv2.bilateralFilter(
        gray, d, sigma_color, sigma_space
    )
    return denoised


# ── Gradient P95 ────────────────────────────────────────────────────────────

def gradient_p95(
    denoised_gray: np.ndarray,
    ksize: int = 5,
    mask: np.ndarray | None = None,
) -> float:
    """Compute the 95th-percentile Sobel gradient magnitude.

    Sobel produces a gradient magnitude map.  Real edges create *strong*
    localised gradients while noise (if still present) creates weak,
    uniformly-distributed gradients.  The 95th percentile captures the
    strength of the best edges in the frame.

    Parameters
    ----------
    denoised_gray : np.ndarray
        Single-channel uint8 grayscale image (H×W), ideally denoised.
    ksize : int, optional
        Sobel kernel size (default 5 for better edge sensitivity).

    Returns
    -------
    float
        95th percentile of the gradient magnitude (≥ 0).
    """
    if denoised_gray is None or denoised_gray.size == 0:
        return 0.0

    if denoised_gray.ndim != 2:
        raise ValueError(
            f"Expected 2-D image, got shape {denoised_gray.shape}"
        )

    # Use CV_64F for signed precision, then magnitude
    grad_x: np.ndarray = cv2.Sobel(denoised_gray, cv2.CV_64F, 1, 0, ksize=ksize)
    grad_y: np.ndarray = cv2.Sobel(denoised_gray, cv2.CV_64F, 0, 1, ksize=ksize)
    magnitude: np.ndarray = np.sqrt(grad_x * grad_x + grad_y * grad_y)

    if mask is not None:
        magnitude = magnitude[mask]
    p95: float = float(np.percentile(magnitude, 95))
    return p95


# ── Body mask (where the player actually is inside their box) ───────────────

# COCO keypoint pairs drawn as thick segments: arms, legs, shoulders, hips, flanks.
_LIMBS = ((5, 7), (7, 9), (6, 8), (8, 10), (11, 13), (13, 15), (12, 14), (14, 16),
          (5, 6), (11, 12), (5, 11), (6, 12))
_KP_MIN_CONF = 0.3
_MIN_BODY_PIXELS = 150


def body_mask(bbox, keypoints, min_conf: float = _KP_MIN_CONF) -> np.ndarray | None:
    """Boolean mask, in *bbox* coordinates, of the pixels that are the player's body.

    Why: a player's box at 2 MP is mostly BACKGROUND — a sponsor banner, the goal net, a fence.
    Those are high-contrast, static and in focus, so the box's P95 gradient measured the
    background: a motion-blurred striker in front of the checkered "EX SCUOLA" banner
    (DSC09526, final 11/7) scored a sharper "subject" than a tack-sharp one. The pose model
    already gives us the skeleton; the body is the torso polygon, thick limb segments and a
    head disc. Returns None when too few keypoints are visible to trust it (caller falls back
    to the whole box, the old behaviour).
    """
    if keypoints is None:
        return None
    x1, y1, x2, y2 = [int(v) for v in bbox]
    w, h = max(0, x2 - x1), max(0, y2 - y1)
    if w < 8 or h < 8:
        return None
    kp = [(float(x) - x1, float(y) - y1, float(c)) for x, y, c in keypoints]
    ok = [c > min_conf for _, _, c in kp]
    if sum(ok) < 4:
        return None
    pt = [(int(round(x)), int(round(y))) for x, y, _ in kp]
    m = np.zeros((h, w), np.uint8)
    grosor = max(3, int(0.07 * h))
    torso = [5, 6, 12, 11]
    if all(ok[i] for i in torso):
        cv2.fillPoly(m, [np.array([pt[i] for i in torso], np.int32)], 1)
    for a, b in _LIMBS:
        if ok[a] and ok[b]:
            cv2.line(m, pt[a], pt[b], 1, grosor)
    cara = [i for i in range(5) if ok[i]]
    if cara:
        cx = int(np.mean([pt[i][0] for i in cara]))
        cy = int(np.mean([pt[i][1] for i in cara]))
        cv2.circle(m, (cx, cy), grosor, 1, -1)
    m = m.astype(bool)
    return m if m.sum() >= _MIN_BODY_PIXELS else None


# ── SNR Sharpness (Global) ─────────────────────────────────────────────────

def snr_sharpness(
    gray: np.ndarray,
    iso: int,
    config: AnalysisConfig = DEFAULT_CONFIG,
    sigma: float | None = None,
    denoised: np.ndarray | None = None,
) -> float:
    """Compute noise-aware SNR sharpness score (full frame).

    Pipeline:
        1. Estimate noise σ from the original image (or use the caller's).
        2. Bilateral-denoise the image (strength scales with ISO).
        3. Compute P95 gradient magnitude on the denoised image.
        4. Return ``P95 / σ`` — the signal-to-noise sharpness ratio.

    Parameters
    ----------
    gray : np.ndarray
        Single-channel uint8 grayscale image (H×W).
    iso : int
        ISO value at capture time.
    config : AnalysisConfig, optional
        Pipeline configuration.
    sigma : float, optional
        Override the noise σ. Defaults to ``config.expected_sigma(iso)``.
    denoised : np.ndarray, optional
        Pre-denoised version of *gray*. The bilateral filter is the single most
        expensive step in the pipeline (d=11 on 2 MP at ISO 8000), so callers
        that need it more than once should compute it once and pass it here.

    Returns
    -------
    float
        SNR sharpness score (higher = sharper relative to noise).
        Returns 0.0 for degenerate inputs.
    """
    if gray is None or gray.size == 0:
        return 0.0

    if gray.ndim != 2:
        raise ValueError(
            f"Expected 2-D grayscale image, got shape {gray.shape}"
        )

    # Step 1: σ PREDICTED FROM ISO, never measured from these pixels.
    # Measuring it here would be circular — blur wipes out the high-frequency
    # noise that the estimator reads, so a blurred frame reports a tiny σ, this
    # divisor collapses, and the blurry shot outscores the sharp one. (That is
    # exactly the bug this metric used to have.) See config.expected_sigma.
    sigma = config.expected_sigma(iso) if sigma is None else max(sigma, config.min_sigma_noise)

    # Step 2: adaptive bilateral denoise (reused if the caller already has it)
    if denoised is None:
        denoised = adaptive_bilateral(gray, iso, config)

    # Step 3: P95 gradient on denoised
    p95: float = gradient_p95(denoised)

    # Step 4: SNR
    snr: float = p95 / sigma
    return snr


# ── SNR Sharpness (Local — Bounding Box) ────────────────────────────────────

def _extract_roi(
    image: np.ndarray,
    bbox: Tuple[int, int, int, int],
) -> np.ndarray | None:
    """Safely extract a ROI from *image* given a (x1, y1, x2, y2) corner box.

    The bounding box uses the *corner* convention ``(x1, y1, x2, y2)`` to
    match the YOLO detectors (:mod:`autocull.analyzers.subject`/``pose``)
    and the rest of the pipeline (``scorer``, ``face``).

    Clamps coordinates to image bounds.  Returns ``None`` if the resulting
    ROI has zero area.
    """
    img_h, img_w = image.shape[:2]
    x1, y1, x2, y2 = bbox

    # Clamp to image bounds
    x1 = max(0, int(x1))
    y1 = max(0, int(y1))
    x2 = min(img_w, int(x2))
    y2 = min(img_h, int(y2))

    if x2 <= x1 or y2 <= y1:
        return None

    return image[y1:y2, x1:x2]


def snr_sharpness_local(
    image_bgr: np.ndarray,
    bbox: Tuple[int, int, int, int],
    iso: int,
    config: AnalysisConfig = DEFAULT_CONFIG,
    sigma: float | None = None,
    denoised_gray: np.ndarray | None = None,
    keypoints=None,
) -> float:
    """Compute noise-aware SNR sharpness within a bounding box.

    With *keypoints* (YOLO-pose, COCO order) the P95 is taken only over the player's body
    (:func:`body_mask`), not the background that fills most of the box.

    Same pipeline as :func:`snr_sharpness` but applied only to the ROI
    defined by *bbox* (typically the YOLO-detected player region).

    Parameters
    ----------
    image_bgr : np.ndarray
        BGR colour image (H×W×3) — as loaded by ``cv2.imread``.
    bbox : tuple of int
        ``(x1, y1, x2, y2)`` corner bounding box in pixel coordinates
        (as returned by the YOLO detectors).
    iso : int
        ISO value at capture time.
    config : AnalysisConfig, optional
        Pipeline configuration.

    Returns
    -------
    float
        SNR sharpness score for the ROI.  Returns 0.0 if the ROI is
        invalid or empty.
    """
    # Preferred path: crop the frame the caller already denoised. Cropping a
    # denoised frame beats denoising a crop — same interior pixels, no border
    # artefacts, and it skips a bilateral filter per player (the pipeline runs
    # this once per detected person, ~6 per frame).
    if denoised_gray is not None:
        roi = _extract_roi(denoised_gray, bbox)
        if roi is None or roi.size == 0 or roi.shape[0] < 8 or roi.shape[1] < 8:
            return 0.0
        sigma = config.expected_sigma(iso) if sigma is None else max(sigma, config.min_sigma_noise)
        mask = body_mask(bbox, keypoints)
        if mask is not None and mask.shape != roi.shape:  # box clamped at the frame edge
            mask = None
        return gradient_p95(roi, mask=mask) / sigma

    if image_bgr is None or image_bgr.size == 0:
        return 0.0

    # Convert to grayscale
    if image_bgr.ndim == 3:
        gray_full: np.ndarray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    elif image_bgr.ndim == 2:
        gray_full = image_bgr
    else:
        return 0.0

    roi = _extract_roi(gray_full, bbox)
    if roi is None or roi.size == 0:
        return 0.0

    # Minimum ROI size for meaningful analysis (at least 8×8)
    rh, rw = roi.shape
    if rh < 8 or rw < 8:
        return 0.0

    return snr_sharpness(roi, iso, config, sigma=sigma)


# ── Self-test ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("sharpness.py — self-test")
    print("=" * 60)

    rng = np.random.default_rng(42)

    # 1. Sharp synthetic image (strong edges) + low noise
    sharp = np.zeros((400, 400), dtype=np.uint8)
    # Checkerboard pattern → lots of edges
    for i in range(0, 400, 40):
        for j in range(0, 400, 40):
            if (i // 40 + j // 40) % 2 == 0:
                sharp[i : i + 40, j : j + 40] = 200
    sharp_noisy = np.clip(
        sharp.astype(np.float64) + rng.normal(0, 5, sharp.shape), 0, 255
    ).astype(np.uint8)

    snr_sharp = snr_sharpness(sharp_noisy, iso=800)
    print(f"[sharp checker, ISO 800]  SNR = {snr_sharp:.2f}")

    # 2. Blurry version of same (Gaussian blur)
    blurry = cv2.GaussianBlur(sharp_noisy, (21, 21), 5.0)
    snr_blurry = snr_sharpness(blurry, iso=800)
    print(f"[blurry checker, ISO 800] SNR = {snr_blurry:.2f}")
    assert snr_sharp > snr_blurry, "Sharp should score higher than blurry!"

    # 3. High ISO noise only (no real edges)
    noise_only = np.clip(
        128 + rng.normal(0, 30, (400, 400)), 0, 255
    ).astype(np.uint8)
    snr_noise = snr_sharpness(noise_only, iso=10000)
    print(f"[noise-only, ISO 10000]   SNR = {snr_noise:.2f}  (expected low)")

    # 4. Local sharpness on a bounding box (x1, y1, x2, y2)
    color_img = cv2.cvtColor(sharp_noisy, cv2.COLOR_GRAY2BGR)
    bbox = (50, 50, 250, 250)  # (x1, y1, x2, y2)
    snr_local = snr_sharpness_local(color_img, bbox, iso=3200)
    print(f"[local ROI, ISO 3200]     SNR = {snr_local:.2f}")

    # 5. Invalid / edge-case bounding box (x2 <= x1)
    snr_invalid = snr_sharpness_local(color_img, (-10, -10, -5, -5), iso=3200)
    print(f"[invalid bbox]            SNR = {snr_invalid:.2f}  (expected 0.0)")

    # 5b. Blurred player in front of a sharp banner (DSC09526, final 11/7): the whole box is
    #     dominated by the banner; the body mask must measure the player, not the background.
    escena = sharp_noisy.copy()
    jugador = cv2.GaussianBlur(np.full((200, 80), 120, np.uint8), (1, 1), 0)
    cv2.rectangle(jugador, (20, 40), (60, 160), 60, -1)          # torso/legs, soft edges below
    jugador = cv2.GaussianBlur(jugador, (31, 31), 9.0)           # movido
    escena[100:300, 160:240] = jugador
    caja = (120, 90, 280, 310)  # like YOLO's box: the player plus the banner around them
    # skeleton of a standing player inside the box: head, shoulders, hips, knees, ankles
    kp = [(200, 115, .9), (195, 112, .9), (205, 112, .9), (190, 115, .5), (210, 115, .5),
          (182, 145, .9), (218, 145, .9), (180, 180, .8), (220, 180, .8), (178, 210, .7), (222, 210, .7),
          (185, 220, .9), (215, 220, .9), (186, 255, .9), (214, 255, .9), (186, 290, .9), (214, 290, .9)]
    den = adaptive_bilateral(escena, 3200)
    snr_caja = snr_sharpness_local(None, caja, 3200, denoised_gray=den)
    snr_cuerpo = snr_sharpness_local(None, caja, 3200, denoised_gray=den, keypoints=kp)
    print(f"[blurred player, sharp banner] box SNR = {snr_caja:.1f}  body SNR = {snr_cuerpo:.1f}")
    assert snr_cuerpo < snr_caja * 0.7, "the body mask must not measure the banner behind the player"
    assert body_mask(caja, kp[:3] + [(0, 0, 0.0)] * 14) is None, "too few keypoints -> fall back to the box"

    # 6. Adaptive bilateral params check
    for iso_val in [400, 800, 3200, 6400, 10000, 25600]:
        d, sc, sp = DEFAULT_CONFIG.get_bilateral_params(iso_val)
        print(f"  ISO {iso_val:>5}: d={d}, σColor={sc}, σSpace={sp}")

    print("\n✓ All tests passed.")
