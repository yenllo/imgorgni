"""
Autocull Configuration — All thresholds, weights, and tunable parameters.

Optimized for night sports photography (fut7) with Sony A7R III at high ISO.
All values are calibrated defaults that can be overridden via CLI flags.
"""

from dataclasses import dataclass, field
from typing import Optional
import os


@dataclass
class AnalysisConfig:
    """Configuration for the analysis pipeline."""

    # ── Input ───────────────────────────────────────────────────────────
    # Supported image extensions (case-insensitive)
    raw_extensions: tuple = (".arw", ".cr2", ".cr3", ".nef", ".raf", ".dng", ".orf", ".rw2")
    jpeg_extensions: tuple = (".jpg", ".jpeg")

    # Target resolution for analysis (megapixels). Images are downsampled
    # to this size before any analysis. Lower = faster, higher = more precise.
    # 2MP is the sweet spot for blur/sharpness detection.
    analysis_megapixels: float = 2.0

    # ── Noise Estimation ────────────────────────────────────────────────
    # Minimum sigma_noise to avoid division-by-zero on very clean images (ISO 100)
    min_sigma_noise: float = 1.0

    # Sensor noise model: sigma ≈ k * sqrt(ISO)  (photon shot noise).
    #
    # This exists because measuring sigma FROM THE IMAGE is circular for a
    # sharpness metric: blur destroys the very high-frequency content that the
    # MAD-of-Laplacian estimator reads, so a motion-blurred frame reports a
    # tiny sigma, the divisor in P95/sigma collapses, and blurry photos OUTSCORE
    # sharp ones. Predicting sigma from ISO breaks that feedback loop — the
    # sensor's noise at a given ISO doesn't care whether the shot came out sharp.
    # It also makes a subject ROI and its frame share one sigma automatically.
    #
    # k fitted on the p75 of measured sigma per ISO over a real A7R III night
    # roll (ISO 8000 → sigma 8.9). Re-fit for a different body/pipeline.
    noise_sigma_per_sqrt_iso: float = 0.0995

    # ── Adaptive Bilateral Filter (Denoise before sharpness) ────────────
    # Parameters scale with ISO. These are the ranges:
    bilateral_params: dict = field(default_factory=lambda: {
        # ISO range: (d, sigmaColor, sigmaSpace)
        800:   (5,  30, 30),   # Low noise: gentle denoise
        3200:  (7,  50, 50),   # Moderate noise
        6400:  (9,  65, 65),   # High noise
        10000: (11, 80, 80),   # Very high noise (A7R III ISO 10000)
        99999: (13, 95, 95),   # Extreme (ISO 12800+)
    })

    # ── Tier 1: Quick Filters ───────────────────────────────────────────
    # Sharpness: percentile-based rejection (absolute across session)
    # Photos below this percentile of SNR sharpness score → REJECT
    sharpness_reject_percentile: float = 15.0

    # Blur (FFT): high-freq energy threshold.
    # Below this → likely motion blur / trepidation
    # This is relative — set after first pass calibration
    blur_reject_percentile: float = 10.0

    # Exposure: underexposure detection
    # Reject if >60% of pixels are in [0-15] range (severely dark)
    underexposure_shadow_threshold: float = 0.60
    underexposure_pixel_max: int = 15

    # Overexposure: reject if >25% of pixels are clipped [245-255]
    overexposure_highlight_threshold: float = 0.25
    overexposure_pixel_min: int = 245

    # ── Tier 2: Subject Analysis ────────────────────────────────────────
    # YOLO person detection
    yolo_model_path: str = ""  # Auto-resolved at runtime
    yolo_pose_model_path: str = "" # Auto-resolved at runtime
    yolo_confidence_threshold: float = 0.3  # Low for night conditions
    yolo_input_size: int = 640  # YOLOv8 default input
    yolo_person_class_id: int = 0  # COCO class 0 = person
    yolo_ball_class_id: int = 32   # COCO class 32 = sports ball

    # The ball anchors protagonist selection, and it's the hardest thing to see
    # at night (small, fast, motion-blurred). Measured on a real roll, ball
    # recall by model/threshold:
    #     yolov8n @0.20 → 39%   @0.10 → 47%    (33 ms/frame)
    #     yolov8s @0.20 → 51%   @0.10 → 58%    (73 ms/frame)
    #     yolov8m @0.20 → 54%   @0.10 → 61%   (186 ms/frame)
    # yolov8s is the sweet spot: +12pp over nano for 2.2x cost, while medium
    # buys only 3pp more for 5.6x. Ball gets its own (lower) threshold because
    # missing the ball costs us the subject anchor entirely.
    yolo_ball_confidence_threshold: float = 0.15

    # Minimum persons detected for a photo to PASS
    min_persons: int = 1

    # ── Protagonist selection ───────────────────────────────────────────
    # Which detected person is the *subject* of the photo. Picking "the
    # sharpest person" is circular: it can never detect that the autofocus
    # missed the action, because it just redefines the subject as whatever
    # came out sharp. So the protagonist is chosen from COMPOSITION only
    # (ball, size, centrality) and sharpness is measured on them afterwards.
    # The ball is detected in only ~48% of night frames, so when it's absent
    # the ball weight is redistributed over size + centrality.
    protagonist_weights: dict = field(default_factory=lambda: {
        "ball_proximity": 0.50,  # closest to the ball = has the action
        "size": 0.30,            # bigger bbox = nearer / framed by the photographer
        "centrality": 0.20,      # photographers put the subject near the centre
    })

    # Subject sharpness: SNR threshold for the player's bounding box
    # Photos where the best subject's SNR < this → REJECT
    subject_snr_reject_percentile: float = 25.0

    # Subject exposure: minimum mean brightness in the player's bbox
    subject_min_brightness: float = 35.0
    subject_max_brightness: float = 245.0

    # ── Scoring Weights ─────────────────────────────────────────────────
    score_weights: dict = field(default_factory=lambda: {
        "snr_sharpness_global": 0.10,      # Tier 1: global noise-aware sharpness
        "fft_blur": 0.05,                  # Tier 1: motion blur / trepidation
        "snr_sharpness_subject": 0.30,     # Tier 2: LOCAL sharpness on player (KEY)
        "subject_exposure": 0.10,          # Tier 2: player brightness
        "detection_confidence": 0.10,      # Tier 2: YOLO confidence
        "composition_ball": 0.15,          # Tier 3: Ball presence bonus
        "composition_crop": 0.10,          # Tier 3: Crop potential margin
        "composition_pose": 0.10,          # Tier 4: Face visibility and dynamic stride
    })

    # Final score percentile for rejection (absolute across session)
    # Everything below this percentile → REJECT
    final_reject_percentile: float = 30.0

    # Burst grouping: photos captured within this many seconds belong to the
    # same burst. Used by both the culler's dedup (scorer) and the review UI
    # grouping (web/state) so they agree.
    burst_window_seconds: float = 1.5

    # ── Performance ─────────────────────────────────────────────────────
    # Number of workers for CPU-bound analysis (multiprocessing)
    num_workers: int = 0  # 0 = auto (cpu_count - 2, min 4)

    # Multiprocessing: chunk size for pool.imap
    chunk_size: int = 2

    # ── Output ──────────────────────────────────────────────────────────
    # Accepted folder name (created inside input directory)
    accepted_folder: str = "_selected"

    # Undo log filename
    undo_log_filename: str = "_autocull_undo.json"

    # Report filenames
    report_csv_filename: str = "autocull_report.csv"
    report_json_filename: str = "autocull_report.json"

    # XMP: values written to rejected photo sidecars
    xmp_reject_rating: int = 1
    xmp_reject_label: str = "Red"

    # ── YOLO Backend ────────────────────────────────────────────────────
    # Execution provider priority: try NPU first, then iGPU, then CPU
    yolo_backend: str = "auto"  # "auto", "cpu", "directml", "npu"

    def __post_init__(self):
        """Resolve auto-config values."""
        if self.num_workers == 0:
            # Cap at 4 workers: each loads YOLO + YOLO-pose + MediaPipe (3 AI models).
            # More workers = massive RAM & initialization overhead with diminishing returns.
            self.num_workers = 4

        if not self.yolo_model_path:
            # Default: models/ directory relative to package.
            # yolov8s (not nano) — see yolo_ball_confidence_threshold for why.
            pkg_dir = os.path.dirname(os.path.abspath(__file__))
            project_dir = os.path.dirname(pkg_dir)
            self.yolo_model_path = os.path.join(project_dir, "models", "yolov8s.onnx")
            
        if not self.yolo_pose_model_path:
            # yolov8s-pose, not nano: the persons this model finds ARE the
            # candidate pool for pick_protagonist, so a player it misses can
            # never be chosen as the subject. Measured on a real roll, s-pose
            # finds 14% more players than nano (median 6 vs 5 per frame) and
            # changes the protagonist in 9% of frames. 71 ms vs 30 ms.
            pkg_dir = os.path.dirname(os.path.abspath(__file__))
            project_dir = os.path.dirname(pkg_dir)
            self.yolo_pose_model_path = os.path.join(project_dir, "models", "yolov8s-pose.onnx")

    def expected_sigma(self, iso: int) -> float:
        """Sensor noise sigma predicted from ISO — the denominator for sharpness.

        Deliberately NOT measured from the pixels: see noise_sigma_per_sqrt_iso.
        """
        iso = max(int(iso), 1)
        return max(self.noise_sigma_per_sqrt_iso * (iso ** 0.5), self.min_sigma_noise)

    def get_bilateral_params(self, iso: int) -> tuple:
        """Get bilateral filter parameters for a given ISO value.

        Returns:
            Tuple of (d, sigmaColor, sigmaSpace) for cv2.bilateralFilter.
        """
        sorted_thresholds = sorted(self.bilateral_params.keys())
        for threshold in sorted_thresholds:
            if iso <= threshold:
                return self.bilateral_params[threshold]
        # Fallback to max
        return self.bilateral_params[sorted_thresholds[-1]]


# Default configuration singleton
DEFAULT_CONFIG = AnalysisConfig()
