"""
Autocull — Motion Blur / Trepidation Detection.

Detects motion blur and camera shake (trepidation) using FFT frequency
analysis.  The key insight: a sharp image has strong *high-frequency*
components (edges, textures), while a motion-blurred image has its energy
concentrated in *low* frequencies.

**Pipeline**:

1. **Bilateral denoise** — Remove grain so it doesn't create false
   high-frequency energy at high ISO.
2. **2-D FFT + shift** — Move zero-frequency to centre.
3. **Mask out low frequencies** — Zero the central disc (radius proportional
   to image size) so we only measure high-freq energy.
4. **High-freq energy ratio** — Sum of magnitudes in high-freq band
   divided by total magnitude.  Higher ratio = sharper.

The threshold for "blurry" is adapted: at higher noise levels a slightly
lower ratio is expected, so the threshold drops proportionally.

Designed for night fut7 photography where shutter speeds of 1/500–1/800
are common but camera shake at 85 mm is still a risk.
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import cv2

from autocull.config import AnalysisConfig, DEFAULT_CONFIG
from autocull.analyzers.noise import estimate_noise


# ── Constants ───────────────────────────────────────────────────────────────
# Fraction of the shorter image dimension used as the low-freq mask radius
_LF_MASK_FRACTION: float = 0.05

# Base threshold for the high-frequency energy ratio.
# Below this → image is considered motion-blurred.
_BASE_FFT_THRESHOLD: float = 0.15

# How much the threshold drops per unit of noise σ
_NOISE_THRESHOLD_SCALE: float = 0.001


# ── FFT Blur Score ──────────────────────────────────────────────────────────

def fft_blur_score(
    gray: np.ndarray,
    denoise: bool = True,
    iso: int = 3200,
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> float:
    """Compute an FFT-based high-frequency energy score.

    A *higher* score indicates more high-frequency content (= sharper).
    A *lower* score suggests motion blur or camera shake.

    Parameters
    ----------
    gray : np.ndarray
        Single-channel uint8 grayscale image (H×W).
    denoise : bool, optional
        Whether to bilateral-denoise before FFT (default ``True``).
        Set to ``False`` if the image is already denoised.
    iso : int, optional
        ISO used for bilateral parameter lookup when *denoise* is ``True``.
    config : AnalysisConfig, optional
        Configuration object.

    Returns
    -------
    float
        High-frequency energy ratio in [0, 1].  0 = all energy is
        low-frequency (extreme blur), 1 = all energy is high-frequency.
        Returns 0.0 for degenerate inputs.
    """
    if gray is None or gray.size == 0:
        return 0.0

    if gray.ndim != 2:
        raise ValueError(
            f"Expected 2-D grayscale image, got shape {gray.shape}"
        )

    h, w = gray.shape
    if h < 16 or w < 16:
        return 0.0

    # ── Optional bilateral denoise ──────────────────────────────────────
    work: np.ndarray
    if denoise:
        d, sigma_color, sigma_space = config.get_bilateral_params(iso)
        work = cv2.bilateralFilter(gray, d, sigma_color, sigma_space)
    else:
        work = gray

    # ── 2-D FFT ─────────────────────────────────────────────────────────
    f_transform: np.ndarray = np.fft.fft2(work.astype(np.float64))
    f_shift: np.ndarray = np.fft.fftshift(f_transform)
    magnitude: np.ndarray = np.abs(f_shift)

    total_energy: float = float(np.sum(magnitude))
    if total_energy < 1e-10:
        return 0.0

    # ── Mask out low frequencies (central disc) ─────────────────────────
    cy, cx = h // 2, w // 2
    radius = int(min(h, w) * _LF_MASK_FRACTION)
    radius = max(radius, 1)  # at least 1 pixel

    # Create circular mask (True = keep, False = zero out)
    yy, xx = np.ogrid[:h, :w]
    dist_sq: np.ndarray = (yy - cy) ** 2 + (xx - cx) ** 2
    lf_mask: np.ndarray = dist_sq > (radius * radius)

    # High-frequency energy
    hf_energy: float = float(np.sum(magnitude[lf_mask]))

    ratio: float = hf_energy / total_energy
    return ratio


# ── Motion Blur Detection ──────────────────────────────────────────────────

def detect_motion_blur(
    gray: np.ndarray,
    iso: int,
    config: AnalysisConfig = DEFAULT_CONFIG,
    sigma: float | None = None,
    denoised: np.ndarray | None = None,
) -> Dict[str, object]:
    """Detect motion blur / camera trepidation.

    Combines the FFT blur score with a noise-adaptive threshold:
    higher noise → slightly lower threshold (because bilateral denoise
    can't remove *all* grain, and some residual HF energy is expected).

    Parameters
    ----------
    gray : np.ndarray
        Single-channel uint8 grayscale image (H×W).
    iso : int
        ISO value at capture time.
    config : AnalysisConfig, optional
        Pipeline configuration.

    Returns
    -------
    dict
        ``{'fft_score': float, 'is_blurry': bool, 'threshold': float,
           'sigma_noise': float}``

        - *fft_score*: high-frequency energy ratio (higher = sharper).
        - *is_blurry*: ``True`` if the score falls below the adaptive
          threshold.
        - *threshold*: the computed adaptive threshold used.
        - *sigma_noise*: estimated noise σ of the input.
    """
    if gray is None or gray.size == 0:
        return {
            "fft_score": 0.0,
            "is_blurry": True,
            "threshold": _BASE_FFT_THRESHOLD,
            "sigma_noise": config.min_sigma_noise,
        }

    # Noise estimation (or reuse the caller's frame σ)
    sigma = estimate_noise(gray, config) if sigma is None else max(sigma, config.min_sigma_noise)

    # FFT score. Reuse the caller's denoised frame when given — otherwise this
    # would run a second bilateral filter over the same pixels that the
    # sharpness analyzer already denoised, the priciest step in the pipeline.
    if denoised is not None:
        score: float = fft_blur_score(denoised, denoise=False, config=config)
    else:
        score = fft_blur_score(gray, denoise=True, iso=iso, config=config)

    # Adaptive threshold: drops slightly with noise
    threshold: float = max(
        _BASE_FFT_THRESHOLD - _NOISE_THRESHOLD_SCALE * sigma,
        0.05,  # floor so we always reject truly blurred images
    )

    is_blurry: bool = score < threshold

    return {
        "fft_score": score,
        "is_blurry": is_blurry,
        "threshold": threshold,
        "sigma_noise": sigma,
    }


# ── Self-test ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("blur.py — self-test")
    print("=" * 60)

    rng = np.random.default_rng(42)

    # 1. Sharp image (checkerboard with noise)
    sharp = np.zeros((400, 400), dtype=np.uint8)
    for i in range(0, 400, 20):
        for j in range(0, 400, 20):
            if (i // 20 + j // 20) % 2 == 0:
                sharp[i : i + 20, j : j + 20] = 200
    sharp_noisy = np.clip(
        sharp.astype(np.float64) + rng.normal(0, 8, sharp.shape), 0, 255
    ).astype(np.uint8)

    score_sharp = fft_blur_score(sharp_noisy, iso=3200)
    result_sharp = detect_motion_blur(sharp_noisy, iso=3200)
    print(f"[sharp checker]  FFT = {score_sharp:.4f}  blurry={result_sharp['is_blurry']}")

    # 2. Motion-blurred version (horizontal kernel)
    kernel_size = 25
    kernel = np.zeros((kernel_size, kernel_size), dtype=np.float32)
    kernel[kernel_size // 2, :] = 1.0 / kernel_size
    blurry = cv2.filter2D(sharp_noisy, -1, kernel)

    score_blurry = fft_blur_score(blurry, iso=3200)
    result_blurry = detect_motion_blur(blurry, iso=3200)
    print(f"[motion-blurred] FFT = {score_blurry:.4f}  blurry={result_blurry['is_blurry']}")

    assert score_sharp > score_blurry, "Sharp should have higher FFT score!"

    # 3. Gaussian-blurred version (camera shake / defocus)
    defocus = cv2.GaussianBlur(sharp_noisy, (31, 31), 8.0)
    score_defocus = fft_blur_score(defocus, iso=3200)
    result_defocus = detect_motion_blur(defocus, iso=3200)
    print(f"[defocused]      FFT = {score_defocus:.4f}  blurry={result_defocus['is_blurry']}")

    # 4. Noise-only image (high ISO, no structure)
    noise_only = np.clip(
        128 + rng.normal(0, 25, (400, 400)), 0, 255
    ).astype(np.uint8)
    score_noise = fft_blur_score(noise_only, iso=10000)
    result_noise = detect_motion_blur(noise_only, iso=10000)
    print(
        f"[noise-only]     FFT = {score_noise:.4f}  blurry={result_noise['is_blurry']}  "
        f"σ={result_noise['sigma_noise']:.1f}  thresh={result_noise['threshold']:.4f}"
    )

    # 5. All-black frame
    black = np.zeros((200, 200), dtype=np.uint8)
    result_black = detect_motion_blur(black, iso=3200)
    print(f"[all-black]      FFT = {result_black['fft_score']:.4f}  blurry={result_black['is_blurry']}")

    # 6. Empty image guard
    empty = np.array([], dtype=np.uint8).reshape(0, 0)
    result_empty = detect_motion_blur(empty, iso=3200)
    print(f"[empty image]    FFT = {result_empty['fft_score']:.4f}  blurry={result_empty['is_blurry']}")

    print("\n✓ All tests passed.")
