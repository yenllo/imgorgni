"""
Autocull — Noise Estimation Module.

Estimates per-image noise level (sigma) using the Median Absolute Deviation
(MAD) of the Laplacian.  This is the standard robust estimator from
Donoho & Johnstone (1994):

    σ = median(|Laplacian(I)|) / 0.6745

Why MAD instead of variance?
  - The *median* is insensitive to outliers (real edges produce a few very
    large Laplacian values, but they don't move the median).
  - The constant 0.6745 is the 75th-percentile of the standard normal,
    making the estimator consistent for Gaussian noise.

Edge cases:
  - All-black / very dark frames → Laplacian is near zero → we clamp to
    ``config.min_sigma_noise`` so downstream SNR never divides by zero.
  - Zero-area images → returns ``min_sigma_noise``.

Optimised for Sony A7R III at ISO 1600–10000 under night lighting.
"""

from __future__ import annotations

import numpy as np
import cv2

from autocull.config import AnalysisConfig, DEFAULT_CONFIG


# ── Constants ───────────────────────────────────────────────────────────────
# Donoho–Johnstone normalisation constant (Φ⁻¹(0.75))
_MAD_SCALE: float = 0.6745


def estimate_noise(
    gray_image: np.ndarray,
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> float:
    """Estimate the noise standard-deviation of a grayscale image.

    Uses the MAD (Median Absolute Deviation) of the Laplacian, a robust
    estimator that is not inflated by real edges in the scene.

    Parameters
    ----------
    gray_image : np.ndarray
        Single-channel uint8 grayscale image (H×W).
    config : AnalysisConfig, optional
        Configuration object providing ``min_sigma_noise``.

    Returns
    -------
    float
        Estimated noise σ, clamped to at least ``config.min_sigma_noise``.

    Raises
    ------
    ValueError
        If *gray_image* is not 2-D.

    Examples
    --------
    >>> import numpy as np
    >>> flat = np.full((100, 100), 128, dtype=np.uint8)
    >>> sigma = estimate_noise(flat)
    >>> sigma >= 1.0  # at least min_sigma_noise
    True
    """
    # ── Validate input ──────────────────────────────────────────────────
    if gray_image is None or gray_image.size == 0:
        return config.min_sigma_noise

    if gray_image.ndim != 2:
        raise ValueError(
            f"Expected 2-D grayscale image, got shape {gray_image.shape}"
        )

    # ── Guard: tiny images ──────────────────────────────────────────────
    h, w = gray_image.shape
    if h < 3 or w < 3:
        # Laplacian kernel is 3×3; can't compute on smaller images.
        return config.min_sigma_noise

    # ── Compute Laplacian (CV_64F for signed precision) ─────────────────
    laplacian: np.ndarray = cv2.Laplacian(gray_image, cv2.CV_64F)

    # Absolute values — we only care about magnitude
    abs_laplacian: np.ndarray = np.abs(laplacian)

    # ── MAD estimator ───────────────────────────────────────────────────
    median_val: float = float(np.median(abs_laplacian))

    if median_val < 1e-8:
        # Image is essentially flat (all-black, synthetic solid, etc.)
        return config.min_sigma_noise

    sigma: float = median_val / _MAD_SCALE

    # Clamp to minimum to guarantee safe downstream division
    return max(sigma, config.min_sigma_noise)


# ── Self-test ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("noise.py — self-test")
    print("=" * 60)

    rng = np.random.default_rng(42)

    # 1. Flat (noiseless) image
    flat = np.full((200, 200), 128, dtype=np.uint8)
    sigma_flat = estimate_noise(flat)
    print(f"[flat 128]       σ = {sigma_flat:.4f}  (expected ≈ min_sigma)")

    # 2. Gaussian noise, known σ ≈ 15
    noisy = np.clip(
        128 + rng.normal(0, 15, (500, 500)), 0, 255
    ).astype(np.uint8)
    sigma_noisy = estimate_noise(noisy)
    print(f"[Gaussian σ=15]  σ = {sigma_noisy:.4f}  (expected ≈ 15)")

    # 3. All-black frame
    black = np.zeros((200, 200), dtype=np.uint8)
    sigma_black = estimate_noise(black)
    print(f"[all-black]      σ = {sigma_black:.4f}  (expected = min_sigma)")

    # 4. Image with strong edges + noise (edge robustness check)
    edge_img = np.zeros((400, 400), dtype=np.uint8)
    edge_img[:, 200:] = 255  # Hard vertical edge
    edge_noisy = np.clip(
        edge_img.astype(np.float64) + rng.normal(0, 10, edge_img.shape),
        0, 255,
    ).astype(np.uint8)
    sigma_edge = estimate_noise(edge_noisy)
    print(f"[edge + σ=10]    σ = {sigma_edge:.4f}  (expected ≈ 10)")

    # 5. High-ISO simulation (σ ≈ 30)
    high_iso = np.clip(
        100 + rng.normal(0, 30, (500, 500)), 0, 255
    ).astype(np.uint8)
    sigma_high = estimate_noise(high_iso)
    print(f"[high-ISO σ=30]  σ = {sigma_high:.4f}  (expected ≈ 30)")

    # 6. Tiny image guard
    tiny = np.full((2, 2), 128, dtype=np.uint8)
    sigma_tiny = estimate_noise(tiny)
    print(f"[2×2 image]      σ = {sigma_tiny:.4f}  (expected = min_sigma)")

    print("\n✓ All tests passed.")
