"""
Pipeline Orchestrator — Ties all modules together into a 2-tier multiprocessing pipeline.
"""

import os
import gc
import logging
from concurrent.futures import ProcessPoolExecutor
import multiprocessing as mp

from autocull.config import AnalysisConfig, DEFAULT_CONFIG
from autocull.extractors.preview import find_images, load_image, downsample_for_analysis
from autocull.extractors.metadata import read_exif, get_iso_or_default
from autocull.analyzers.noise import estimate_noise
from autocull.analyzers.sharpness import snr_sharpness, snr_sharpness_local, adaptive_bilateral  # noqa: F401
from autocull.analyzers.blur import detect_motion_blur
from autocull.analyzers.exposure import analyze_exposure_global, analyze_exposure_local
from autocull.analyzers.subject import detect_persons_simple, SubjectDetector
from autocull.analyzers.pose import PoseDetector, evaluate_pose
from autocull.analyzers.face import evaluate_facial_expression
from autocull.scoring.scorer import compute_tier1_result, compute_tier2_result, compute_final_scores

import cv2
import numpy as np


logger = logging.getLogger(__name__)

# Global detector instance for each worker process
worker_detector = None
worker_pose_detector = None

def init_worker(config: AnalysisConfig):
    """Initialize the worker process.
    
    Sets up the YOLO detector once per process to avoid the massive overhead
    of initializing an ONNX Runtime session for every single image.
    """
    global worker_detector
    global worker_pose_detector
    
    # Set thread limits inside the worker (spawn'd processes don't inherit env vars on Windows)
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    os.environ["GLOG_minloglevel"] = "3"
    os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
    
    import warnings
    warnings.filterwarnings("ignore", category=UserWarning, module="onnxruntime")
    
    # Redirect stderr to suppress MediaPipe/TFLite C++ warnings during model loading
    import sys
    _real_stderr = sys.stderr
    sys.stderr = open(os.devnull, 'w')
    
    try:
        import cv2
        cv2.setNumThreads(1)
        
        worker_detector = SubjectDetector(config)
        worker_detector.initialize()
        
        worker_pose_detector = PoseDetector(config)
        worker_pose_detector.initialize()
        
        # Pre-load face landmarker
        from autocull.analyzers.face import _get_face_landmarker
        _get_face_landmarker()
        
    except Exception as e:
        logging.error(f"Failed to initialize models in worker: {e}")
        worker_detector = None
        worker_pose_detector = None
    finally:
        sys.stderr.close()
        sys.stderr = _real_stderr


def process_single_image(args) -> dict:
    """Process a single image through the 2-tier pipeline.
    
    Args:
        args: Tuple of (file_path, config)
        
    Returns:
        Dict with analysis results.
    """
    file_path, config = args
    
    result = {
        "filename": os.path.basename(file_path),
        "filepath": file_path,
        "error": None,
        "exif": {},
        "tier1": {},
        "tier2": None,
    }
    
    try:
        # 1. Read EXIF
        exif_data = read_exif(file_path)
        iso = get_iso_or_default(exif_data, default_iso=3200)
        result["exif"] = exif_data
        
        # 2. Load and downsample image
        img_bgr = load_image(file_path, config)
        if img_bgr is None:
            raise ValueError(f"Could not load image: {file_path}")
            
        img_small = downsample_for_analysis(img_bgr, config.analysis_megapixels)
        gray_small = cv2.cvtColor(img_small, cv2.COLOR_BGR2GRAY)
        
        # --- TIER 1: Quick Filters (Noise-aware) ---
        # 1. Measured noise — used ONLY by the blur detector's adaptive
        #    threshold. Sharpness must NOT use it: blur collapses the measured
        #    σ, which would inflate P95/σ and make blurry photos win. Sharpness
        #    takes σ from the ISO instead (config.expected_sigma).
        sigma_noise = estimate_noise(gray_small, config)
        if sigma_noise < config.min_sigma_noise:
            sigma_noise = config.min_sigma_noise

        # 2. Bilateral denoise ONCE. It's the single priciest op in the pipeline
        #    (d=11 over 2 MP at ISO 8000) and three consumers need it: global
        #    sharpness, the blur FFT, and every subject ROI. Computing it per
        #    consumer meant ~8 bilaterals per photo instead of one.
        denoised_small = adaptive_bilateral(gray_small, iso, config)

        # 3. Sharpness (P95 / σ(ISO))
        sharpness_score = snr_sharpness(gray_small, iso, config, denoised=denoised_small)

        # 4. Blur (FFT on the same denoised frame)
        blur_data = detect_motion_blur(gray_small, iso, config, sigma=sigma_noise,
                                       denoised=denoised_small)
        
        # 4. Exposure global
        exposure_data = analyze_exposure_global(gray_small, config)
        
        tier1 = compute_tier1_result(
            sharpness_score, 
            blur_data["fft_score"], 
            exposure_data, 
            config
        )
        result["tier1"] = tier1
        
        # --- TIER 2: Subject Analysis ---
        # Only run if Tier 1 passed
        if tier1["tier1_pass"]:
            # YOLO detection
            global worker_detector
            global worker_pose_detector
            
            # SubjectDetector (yolov8n) gives us the ball
            detections = detect_persons_simple(img_small, config, detector=worker_detector)
            balls = [d for d in detections if d["class_id"] == config.yolo_ball_class_id]
            
            # PoseDetector (yolov8n-pose) gives us persons with keypoints
            persons = worker_pose_detector.detect_persons(img_small) if worker_pose_detector else []
            
            subject_snr_scores = []
            subject_exposure_data = []
            face_expression_data = []
            
            for det in persons:
                bbox = det["bbox"]
                # Crop the already-denoised frame; σ comes from the ISO, so
                # every ROI and the frame share it.
                snr = snr_sharpness_local(img_small, bbox, iso, config,
                                          denoised_gray=denoised_small,
                                          keypoints=det.get("keypoints"))
                exp = analyze_exposure_local(img_small, bbox, config)
                face = evaluate_facial_expression(img_small, bbox)
                
                subject_snr_scores.append(snr)
                subject_exposure_data.append(exp)
                face_expression_data.append(face)
                
            tier2 = compute_tier2_result(
                persons, 
                subject_snr_scores, 
                subject_exposure_data, 
                face_expression_data,
                balls,
                img_small.shape,
                config
            )
            result["tier2"] = tier2
            
    except Exception as e:
        result["error"] = str(e)
        
    finally:
        # Force garbage collection for OpenCV/NumPy objects
        # We don't want memory leaks in multiprocessing workers
        gc.collect()
        
    return result


def run_pipeline(
    input_dir: str, 
    config: AnalysisConfig = DEFAULT_CONFIG,
    progress_callback=None,
    files: list[str] | None = None,
    ranker: str | None = None,
) -> list[dict]:
    """Run the Autocull pipeline on a directory of images.
    
    Args:
        input_dir: Directory containing images. Ignored when *files* is given.
        config: Analysis configuration.
        progress_callback: Optional callable(int, int) -> None for progress updates.
        files: Explicit list of image paths to analyze instead of scanning a
            directory. El curador de IMGorgni manda las fotos de un evento, que
            viven desparramadas en varias carpetas de K: y no forman una sola.
        ranker: Path to a ranker JSON from ``python -m autocull aprender``. Same as
            setting ``config.ranker_path``; it reorders the survivors of the hard filters.

    Returns:
        List of result dictionaries.
    """
    if ranker:
        config.ranker_path = ranker
    if files is None:
        files, fmt = find_images(input_dir, config)
    total_files = len(files)
    
    if total_files == 0:
        return []
        
    # Prepare arguments for multiprocessing
    tasks = [(f, config) for f in files]
    results = []
    
    workers = config.num_workers
    # Limit workers if we have fewer files than workers
    workers = min(workers, total_files)
    
    # Use spawn context to avoid issues with OpenCV in multiprocessing on some platforms
    ctx = mp.get_context('spawn')

    # ponytail: worker memory strategy is per-image gc.collect() (see
    # process_single_image); models are constant-size so there's no leak to
    # chase. If a giant roll ever shows growth, pass max_tasks_per_child with a
    # HIGH value (~500) — not low, or workers reload 3 models constantly.
    
    with ProcessPoolExecutor(
        max_workers=workers, 
        mp_context=ctx,
        initializer=init_worker,
        initargs=(config,)
    ) as executor:
        # Use map to preserve order
        for i, res in enumerate(executor.map(process_single_image, tasks, chunksize=config.chunk_size)):
            results.append(res)
            if progress_callback:
                progress_callback(i + 1, total_files)
                
    # ── Final Scoring (Session-wide Percentiles) ──
    # We do this after all images are processed
    valid_results = [r for r in results if r["error"] is None]
    
    if valid_results:
        valid_results = compute_final_scores(valid_results, config)
        
    # Recombine with error results
    final_results = []
    for r in results:
        if r["error"] is not None:
            r["decision"] = "ERROR"
            r["reject_reason"] = r["error"]
        final_results.append(r)
        
    return final_results

