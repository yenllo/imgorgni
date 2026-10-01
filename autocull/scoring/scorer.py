"""
Scorer — Composite quality scoring and pass/reject decisions.

Combines all analyzer results into a single score per photo and makes
the final PASS/REJECT decision based on session-wide percentile ranking.

The scoring is absolute across the entire session (not per-burst).
"""

import os

import numpy as np
from typing import Optional

from autocull.config import AnalysisConfig, DEFAULT_CONFIG
from autocull.analyzers.pose import evaluate_pose


def _rank_map(indices, all_results, getter):
    """Map each result index → percentile rank [0-100] of ``getter(result)``.

    Rank 0 = lowest value in the set, 100 = highest. Unlike fixed
    multipliers (``min(snr * 10, 100)``), percentile ranking is robust to
    the absolute scale of the metric: real-game sharpness scores in the
    60-280 range still discriminate instead of all saturating at the cap.

    Args:
        indices: Result indices that participate in composite scoring.
        all_results: The full results list.
        getter: Callable extracting the numeric metric from a result dict.

    Returns:
        Dict mapping ``result index -> rank in [0, 100]``.
    """
    if not indices:
        return {}

    vals = np.array([float(getter(all_results[i])) for i in indices])
    n = len(vals)
    if n == 1:
        return {indices[0]: 100.0}

    order = vals.argsort()
    ranks = np.empty(n)
    ranks[order] = np.arange(n)
    ranks = ranks / (n - 1) * 100.0
    return {indices[k]: float(ranks[k]) for k in range(n)}


# Ball-proximity decay scale, in multiples of the player's own bbox height.
# The ball is "theirs" if it sits within roughly this reach of their box.
_BALL_REACH_BODY_HEIGHTS: float = 0.75


def _bbox_point_distance(bbox, point) -> float:
    """Distance from *point* to the nearest edge of *bbox* (0.0 if inside)."""
    x1, y1, x2, y2 = bbox
    px, py = point
    dx = max(x1 - px, 0.0, px - x2)
    dy = max(y1 - py, 0.0, py - y2)
    return float(np.hypot(dx, dy))


def pick_protagonist(
    persons: list[dict],
    balls: list[dict],
    image_shape: tuple,
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> int:
    """Index of the person the photo is *about* — chosen from composition only.

    Deliberately ignores sharpness. Ranking subjects by sharpness is circular:
    it redefines the subject as whoever came out sharp, so a frame where the
    autofocus missed the action (blurred striker, tack-sharp defender in the
    background) scores as a keeper. Choosing on composition and *then* grading
    that person's sharpness is what lets the culler reject those.

    Signals, each normalised to [0, 1] *within the frame* so this is a ranking
    among the candidates present:
      - ball_proximity: 1.0 when the ball touches the player's box, decaying
        with distance measured in *body heights* (not image diagonal — that
        compressed every player into 0.7-1.0 and made the signal useless).
        Only ~48% of night frames have a detected ball; when absent its weight
        is redistributed over the other two.
      - size: bbox area relative to the largest person in frame.
      - centrality: how close to the frame centre the player sits.

    Returns:
        Index into *persons*. 0 if the list is degenerate.
    """
    if not persons:
        return 0
    if len(persons) == 1:
        return 0

    w = config.protagonist_weights
    img_h, img_w = image_shape[:2]
    diag = float(np.hypot(img_w, img_h)) or 1.0
    cx, cy = img_w / 2.0, img_h / 2.0

    areas = []
    for p in persons:
        x1, y1, x2, y2 = p["bbox"]
        areas.append(max(1.0, (x2 - x1) * (y2 - y1)))
    max_area = max(areas)

    ball_centre = None
    if balls:
        bx1, by1, bx2, by2 = balls[0]["bbox"]
        ball_centre = ((bx1 + bx2) / 2.0, (by1 + by2) / 2.0)

    # No ball detected → redistribute its weight over size + centrality.
    w_ball = w.get("ball_proximity", 0.5) if ball_centre else 0.0
    w_size = w.get("size", 0.3)
    w_centre = w.get("centrality", 0.2)
    if ball_centre is None:
        rest = w_size + w_centre
        if rest > 0:
            w_size, w_centre = w_size / rest, w_centre / rest

    best_idx, best_score = 0, -1.0
    for i, p in enumerate(persons):
        x1, y1, x2, y2 = p["bbox"]
        px, py = (x1 + x2) / 2.0, (y1 + y2) / 2.0

        size = areas[i] / max_area
        centrality = 1.0 - min(1.0, float(np.hypot(px - cx, py - cy)) / (diag / 2.0))
        ball_prox = 0.0
        if ball_centre is not None:
            # Decay in body heights: touching the box ≈ 1.0, ~0.5 at a body
            # height away, ~0 several away. Perspective-correct — a near (big)
            # player legitimately reaches further in pixels than a distant one.
            reach = max(1.0, (y2 - y1) * _BALL_REACH_BODY_HEIGHTS)
            ball_prox = 1.0 / (1.0 + _bbox_point_distance(p["bbox"], ball_centre) / reach)

        score = w_ball * ball_prox + w_size * size + w_centre * centrality
        if score > best_score:
            best_idx, best_score = i, score

    return best_idx


def compute_tier1_result(
    snr_sharpness: float,
    fft_blur_score: float,
    exposure_data: dict,
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> dict:
    """Compute Tier 1 analysis result for a single photo.

    Tier 1 uses CPU-only, fast metrics to reject obviously bad photos
    before running expensive YOLO inference.

    Args:
        snr_sharpness: Noise-aware sharpness score (P95/sigma).
        fft_blur_score: FFT high-frequency energy score.
        exposure_data: Dict from exposure analyzer with shadow/highlight ratios.
        config: Analysis configuration.

    Returns:
        Dict with Tier 1 results:
        - snr_sharpness: float
        - fft_blur: float
        - exposure: dict
        - tier1_pass: bool (True if passes all Tier 1 checks)
        - tier1_reject_reason: str or None
    """
    result = {
        "snr_sharpness": snr_sharpness,
        "fft_blur": fft_blur_score,
        "exposure": exposure_data,
        "tier1_pass": True,
        "tier1_reject_reason": None,
    }

    # Check exposure first (cheapest check, most obvious rejects)
    if exposure_data.get("severely_underexposed", False):
        result["tier1_pass"] = False
        result["tier1_reject_reason"] = "severely_underexposed"
        return result

    if exposure_data.get("blown_highlights", False):
        result["tier1_pass"] = False
        result["tier1_reject_reason"] = "blown_highlights"
        return result

    # Sharpness and blur are checked via percentile later (need all photos first)
    # For now, mark as pass — the pipeline will do percentile-based rejection
    return result


def compute_tier2_result(
    persons: list[dict],
    subject_snr_scores: list[float],
    subject_exposure_data: list[dict],
    face_expression_data: list[dict],
    balls: list[dict],
    image_shape: tuple,
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> dict:
    """Compute Tier 2 & 3 analysis result for a single photo.

    Tier 2 uses YOLO person detection + local analysis on the player.
    Tier 3 (Composition) evaluates ball presence and crop potential.

    Args:
        persons: List of YOLO detections for persons.
        subject_snr_scores: List of SNR sharpness scores for each person.
        subject_exposure_data: List of exposure data for each person.
        balls: List of YOLO detections for sports balls.
        image_shape: Tuple of (H, W, C) for the original image.
        config: Analysis configuration.

    Returns:
        Dict with Tier 2 results:
        - persons_detected: int
        - best_subject_snr: float
        - best_subject_confidence: float
        - best_subject_brightness: float
        - tier2_pass: bool
        - tier2_reject_reason: str or None
    """
    # Can we actually tell WHO the subject is? Only with a ball to anchor on,
    # or when there's a single person (no ambiguity). Without that, the pick is
    # a guess from size/centrality — good enough to rank a photo, but NOT good
    # enough to hard-reject it for "the subject is soft/dark", because we don't
    # know which player the subject is. Those rejections are gated on this.
    protagonist_confident = len(balls) > 0 or len(persons) == 1

    result = {
        "persons_detected": len(persons),
        "best_subject_snr": 0.0,
        "best_subject_confidence": 0.0,
        "best_subject_brightness": 0.0,
        "has_ball": len(balls) > 0,
        "protagonist_confident": protagonist_confident,
        "crop_potential": 0.0,
        "pose_face_visible": 0.0,
        "pose_dynamic_stride": 0.0,
        "face_eyes_closed": False,
        "face_bad_grimace": False,
        "face_expression_penalty": 0.0,
        "tier2_pass": True,
        "tier2_reject_reason": None,
    }

    # No persons detected → frame is empty / no subject
    if len(persons) < config.min_persons:
        result["tier2_pass"] = False
        result["tier2_reject_reason"] = "no_subject"
        return result

    # Pick the subject by COMPOSITION (ball / size / centrality), then grade
    # *that* person's sharpness. Picking the sharpest person instead would be
    # circular — it can never notice that the autofocus missed the action.
    if subject_snr_scores:
        best_idx = pick_protagonist(persons, balls, image_shape, config)
        result["protagonist_idx"] = best_idx
        result["best_subject_snr"] = subject_snr_scores[best_idx]
        result["best_subject_confidence"] = persons[best_idx]["confidence"]
        
        # Calculate Crop Potential (Tier 3)
        img_h, img_w = image_shape[:2]
        p_x1, p_y1, p_x2, p_y2 = persons[best_idx]["bbox"]
        
        min_x, min_y, max_x, max_y = p_x1, p_y1, p_x2, p_y2
        
        if balls:
            b_x1, b_y1, b_x2, b_y2 = balls[0]["bbox"]
            min_x = min(min_x, b_x1)
            min_y = min(min_y, b_y1)
            max_x = max(max_x, b_x2)
            max_y = max(max_y, b_y2)
            
        action_w = max(1, max_x - min_x)
        action_h = max(1, max_y - min_y)
        
        margin_w = (img_w - action_w) / img_w
        margin_h = (img_h - action_h) / img_h
        
        # If margin > 15%, full crop potential (room to reframe). If < 5%, zero potential.
        min_margin = min(margin_w, margin_h)
        potential = (min_margin - 0.05) / 0.10
        result["crop_potential"] = max(0.0, min(1.0, potential))
        
        # Evaluate Pose Biomechanics
        pose_data = evaluate_pose(persons[best_idx])
        result["pose_face_visible"] = pose_data["face_visible"]
        result["pose_dynamic_stride"] = pose_data["dynamic_stride"]
        
        # Evaluate Facial Expression
        if face_expression_data and best_idx < len(face_expression_data):
            face = face_expression_data[best_idx]
            result["face_eyes_closed"] = face.get("eyes_closed", False)
            result["face_bad_grimace"] = face.get("bad_grimace", False)
            result["face_expression_penalty"] = face.get("expression_penalty", 0.0)

        if subject_exposure_data and best_idx < len(subject_exposure_data):
            result["best_subject_brightness"] = subject_exposure_data[best_idx].get(
                "mean_brightness", 0.0
            )

            # Check if the best subject is too dark — only when we actually know
            # who the subject is (see protagonist_confident above).
            if protagonist_confident and subject_exposure_data[best_idx].get("subject_too_dark", False):
                result["tier2_pass"] = False
                result["tier2_reject_reason"] = "subject_too_dark"
                return result

    # Subject SNR is checked via percentile later (need all photos first)
    return result


def compute_final_scores(
    all_results: list[dict],
    config: AnalysisConfig = DEFAULT_CONFIG,
) -> list[dict]:
    """Compute final scores and decisions for all photos in the session.

    Uses session-wide percentile ranking to determine PASS/REJECT.
    This is called after all individual analyses are complete.

    Args:
        all_results: List of result dicts (one per photo), each containing:
            - filename: str
            - tier1: dict from compute_tier1_result
            - tier2: dict from compute_tier2_result (or None if rejected at Tier 1)
        config: Analysis configuration.

    Returns:
        Updated list with final_score and decision added to each result.
    """
    # Separate Tier 1 passes from rejects
    tier1_passed_indices = []
    for i, r in enumerate(all_results):
        if r["tier1"]["tier1_pass"]:
            tier1_passed_indices.append(i)

    # ── Percentile-based Tier 1 rejection (sharpness + blur) ──────────
    if tier1_passed_indices:
        # Collect sharpness scores from ALL photos (including Tier 1 pass candidates)
        all_sharpness = np.array([
            all_results[i]["tier1"]["snr_sharpness"] for i in tier1_passed_indices
        ])
        all_blur = np.array([
            all_results[i]["tier1"]["fft_blur"] for i in tier1_passed_indices
        ])

        # Calculate percentile thresholds
        sharpness_threshold = np.percentile(all_sharpness, config.sharpness_reject_percentile)
        blur_threshold = np.percentile(all_blur, config.blur_reject_percentile)

        # Apply percentile-based rejection
        for i in tier1_passed_indices:
            r = all_results[i]
            if r["tier1"]["snr_sharpness"] < sharpness_threshold:
                r["tier1"]["tier1_pass"] = False
                r["tier1"]["tier1_reject_reason"] = "global_blur_snr"
            elif r["tier1"]["fft_blur"] < blur_threshold:
                r["tier1"]["tier1_pass"] = False
                r["tier1"]["tier1_reject_reason"] = "motion_blur_fft"

    # ── Percentile-based Tier 2 rejection (subject sharpness) ─────────
    tier2_passed_indices = []
    for i, r in enumerate(all_results):
        if r["tier1"]["tier1_pass"] and r.get("tier2") and r["tier2"]["tier2_pass"]:
            tier2_passed_indices.append(i)

    # Only photos where the protagonist is unambiguous (ball present, or a lone
    # person) may be rejected for a soft subject — and only those set the bar.
    # Elsewhere the "subject" is a guess from size/centrality, so both judging
    # it and letting it skew the threshold would reject photos on a coin flip.
    # Those frames still face the global sharpness/blur cuts and the final
    # percentile, which don't depend on knowing who the subject is.
    confident_indices = [
        i for i in tier2_passed_indices
        if all_results[i]["tier2"].get("protagonist_confident", True)
    ]

    if confident_indices:
        subject_snr_scores = np.array([
            all_results[i]["tier2"]["best_subject_snr"] for i in confident_indices
        ])

        subject_snr_threshold = np.percentile(
            subject_snr_scores, config.subject_snr_reject_percentile
        )

        for i in confident_indices:
            r = all_results[i]
            if r["tier2"]["best_subject_snr"] < subject_snr_threshold:
                r["tier2"]["tier2_pass"] = False
                r["tier2"]["tier2_reject_reason"] = "subject_out_of_focus"

    # ── Compute final composite score ─────────────────────────────────
    # Sharpness metrics span very different absolute ranges depending on ISO,
    # lens and scene (real games produce SNR of 60-280, not 0-10), so fixed
    # multipliers like ``min(snr * 10, 100)`` saturate at the cap and the
    # metric stops discriminating between photos. Instead we rank each
    # sharpness metric *within the session* and use its percentile rank
    # [0-100]. This auto-calibrates to any range and makes the configured
    # weights actually drive the ranking.
    weights = config.score_weights

    # Photos that reach composite scoring (passed Tier 1 and, if present, Tier 2)
    composite_indices = [
        i for i, r in enumerate(all_results)
        if r["tier1"]["tier1_pass"]
        and (r.get("tier2") is None or r["tier2"]["tier2_pass"])
    ]

    # Percentile-rank maps for the metrics that previously saturated
    rank_global = _rank_map(composite_indices, all_results,
                            lambda r: r["tier1"]["snr_sharpness"])
    rank_fft = _rank_map(composite_indices, all_results,
                         lambda r: r["tier1"]["fft_blur"])
    rank_subject = _rank_map(composite_indices, all_results,
                             lambda r: (r.get("tier2") or {}).get("best_subject_snr", 0.0))

    for i, r in enumerate(all_results):
        score = 0.0
        tier1 = r["tier1"]
        tier2 = r.get("tier2")

        # If rejected at Tier 1, score is 0
        if not tier1["tier1_pass"]:
            r["final_score"] = 0.0
            r["decision"] = "REJECT"
            r["reject_reason"] = tier1["tier1_reject_reason"]
            r["reject_tier"] = 1
            continue

        # If no Tier 2 data (subject detection disabled): rank on global sharpness only
        if tier2 is None:
            r["final_score"] = round(rank_global.get(i, 0.0), 2)
            r["decision"] = "PASS"
            r["reject_reason"] = None
            r["reject_tier"] = None
            continue

        # If rejected at Tier 2
        if not tier2["tier2_pass"]:
            r["final_score"] = 0.0
            r["decision"] = "REJECT"
            r["reject_reason"] = tier2["tier2_reject_reason"]
            r["reject_tier"] = 2
            continue

        # Sharpness components: session percentile rank [0-100] (no saturation)
        score += weights["snr_sharpness_global"] * rank_global.get(i, 0.0)
        score += weights["fft_blur"] * rank_fft.get(i, 0.0)
        score += weights["snr_sharpness_subject"] * rank_subject.get(i, 0.0)

        # Subject brightness: 0-100 scale (0=black, 100=well-lit)
        brightness = tier2.get("best_subject_brightness", 128)
        brightness_score = min(brightness / 1.28, 100)  # 128 brightness → 100 score
        score += weights["subject_exposure"] * brightness_score

        # Detection confidence: already 0-1, scale to 0-100
        confidence = tier2.get("best_subject_confidence", 0.5)
        score += weights.get("detection_confidence", 0.10) * confidence * 100

        # Tier 3: Composition Bonuses
        if tier2.get("has_ball", False):
            score += weights.get("composition_ball", 0.15) * 100

        crop_potential = tier2.get("crop_potential", 0.0)
        score += weights.get("composition_crop", 0.10) * crop_potential * 100

        # Tier 4: Pose Bonus (average of face visibility and dynamic stride)
        face_v = tier2.get("pose_face_visible", 0.0)
        stride_v = tier2.get("pose_dynamic_stride", 0.0)
        pose_bonus = (face_v + stride_v) / 2.0
        score += weights.get("composition_pose", 0.10) * pose_bonus * 100

        # Facial Expression Penalty (eyes closed / grimace)
        penalty_ratio = tier2.get("face_expression_penalty", 0.0)
        score -= (penalty_ratio * 30.0)  # Subtract up to 30 points

        r["final_score"] = round(score, 2)
        r["decision"] = "PASS"
        r["reject_reason"] = None
        r["reject_tier"] = None

    # ── Tier 5: Burst Deduplication (Group by time) ───────────────────
    # Get all passing photos
    passing_photos = [r for r in all_results if r.get("decision") == "PASS"]
    
    if len(passing_photos) > 1:
        # Sort chronologically. Prefer the real EXIF capture time; fall back
        # to filename only when a timestamp is missing (e.g. metadata-stripped
        # exports). Sorting by filename alone breaks for non-sequential names
        # like digiKam's "BatchTool-*" temp files.
        def _chrono_key(r):
            dt = (r.get("exif") or {}).get("datetime_obj")
            return (0, dt) if dt is not None else (1, r.get("filename", ""))

        passing_photos.sort(key=_chrono_key)
        
        burst_groups = []
        current_group = [passing_photos[0]]
        
        for i in range(1, len(passing_photos)):
            prev = passing_photos[i-1]
            curr = passing_photos[i]
            
            # Since we don't have exact sub-seconds right now, we can group 
            # by EXIF timestamp difference <= 1 second.
            # We'll use a rough heuristic: if EXIF isn't perfect, we can fallback
            # but usually for sports it's highly sequential.
            try:
                # Use exif datetime if available (1-second precision)
                if (prev.get("exif") and prev["exif"].get("datetime_obj") and 
                    curr.get("exif") and curr["exif"].get("datetime_obj")):
                    diff = abs((curr["exif"]["datetime_obj"] - prev["exif"]["datetime_obj"]).total_seconds())
                else:
                    # Fallback to mtime if exif datetime is absent
                    t_prev = os.path.getmtime(prev["filepath"])
                    t_curr = os.path.getmtime(curr["filepath"])
                    diff = abs(t_curr - t_prev)
            except Exception:
                diff = 0.0
                
            if diff <= config.burst_window_seconds:  # same burst
                current_group.append(curr)
            else:
                burst_groups.append(current_group)
                current_group = [curr]
                
        if current_group:
            burst_groups.append(current_group)
            
        # Keep only the highest scorer per burst group
        for group in burst_groups:
            if len(group) > 1:
                # Sort descending by final score
                group.sort(key=lambda x: x["final_score"], reverse=True)
                # Keep the best 1
                for loser in group[1:]:
                    loser["decision"] = "REJECT"
                    loser["reject_reason"] = "burst_duplicate"
                    loser["reject_tier"] = 5

    # ── Final percentile cutoff ───────────────────────────────────────
    # After all component scoring, apply the final percentile cutoff
    pass_scores = [r["final_score"] for r in all_results if r["decision"] == "PASS"]

    if pass_scores:
        final_threshold = np.percentile(pass_scores, config.final_reject_percentile)

        for r in all_results:
            if r["decision"] == "PASS" and r["final_score"] < final_threshold:
                r["decision"] = "REJECT"
                r["reject_reason"] = "below_final_percentile"
                r["reject_tier"] = 3  # Final percentile cut

    return all_results


if __name__ == "__main__":
    # ── pick_protagonist: composition-only subject selection ──────────────
    _cfg = DEFAULT_CONFIG
    _shape = (900, 1200, 3)  # H, W, C

    def _p(x1, y1, x2, y2):
        return {"bbox": (x1, y1, x2, y2), "confidence": 0.9}

    # No ball: the big, central player beats the small one in the corner.
    small_corner, big_centre = _p(10, 10, 60, 120), _p(500, 350, 700, 800)
    assert pick_protagonist([small_corner, big_centre], [], _shape, _cfg) == 1

    # Ball decides between two comparable players: the one it sits on wins,
    # even though the other is marginally more central.
    left, right = _p(300, 350, 480, 800), _p(620, 350, 800, 800)
    ball_on_left = [{"bbox": (470, 760, 500, 790)}]
    assert pick_protagonist([left, right], ball_on_left, _shape, _cfg) == 0
    ball_on_right = [{"bbox": (600, 760, 630, 790)}]
    assert pick_protagonist([left, right], ball_on_right, _shape, _cfg) == 1

    # Selection must NOT depend on sharpness — it never sees it. A blurred
    # striker on the ball still wins over a tack-sharp defender upfield;
    # that's what lets the culler notice the autofocus missed.
    assert pick_protagonist([left, right], ball_on_left, _shape, _cfg) == 0

    # Degenerate inputs stay safe.
    assert pick_protagonist([], [], _shape, _cfg) == 0
    assert pick_protagonist([left], [], _shape, _cfg) == 0
    print("pick_protagonist: OK (size/centrality, ball override, degenerate)")

    # Quick test with synthetic data
    test_results = [
        {
            "filename": "test1.jpg",
            "tier1": {"snr_sharpness": 5.0, "fft_blur": 12.0,
                      "exposure": {"severely_underexposed": False, "blown_highlights": False},
                      "tier1_pass": True, "tier1_reject_reason": None},
            "tier2": {"persons_detected": 2, "best_subject_snr": 4.5,
                      "best_subject_confidence": 0.85, "best_subject_brightness": 120.0,
                      "tier2_pass": True, "tier2_reject_reason": None},
        },
        {
            "filename": "test2.jpg",
            "tier1": {"snr_sharpness": 1.0, "fft_blur": 3.0,
                      "exposure": {"severely_underexposed": False, "blown_highlights": False},
                      "tier1_pass": True, "tier1_reject_reason": None},
            "tier2": {"persons_detected": 0, "best_subject_snr": 0,
                      "best_subject_confidence": 0, "best_subject_brightness": 0,
                      "tier2_pass": False, "tier2_reject_reason": "no_subject"},
        },
    ]

    scored = compute_final_scores(test_results)
    for r in scored:
        print(f"{r['filename']}: score={r['final_score']}, decision={r['decision']}, "
              f"reason={r.get('reject_reason')}")
