"""
Reporter — CSV and JSON report generation for Autocull results.

Generates structured reports from the analysis pipeline output, including:
- Per-image CSV reports with all metric columns
- JSON reports with full results + aggregate statistics
- Summary statistics for the rich CLI dashboard

All output uses Python stdlib only (csv, json, os, pathlib, datetime).
"""

import csv
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# CSV column order. The pipeline produces *nested* result dicts, so
# ``_flatten_result`` maps them onto these flat columns before writing.
CSV_COLUMNS: list[str] = [
    "filename",
    "tier1_snr_sharpness",
    "tier1_fft",
    "tier1_exposure_shadows",
    "tier1_exposure_highlights",
    "tier2_persons_detected",
    "tier2_best_subject_snr",
    "tier2_subject_brightness",
    "has_ball",
    "crop_potential",
    "pose_face_visible",
    "pose_dynamic_stride",
    "face_eyes_closed",
    "face_bad_grimace",
    "final_score",
    "decision",
    "reject_reason",
    "reject_tier",
    "iso",
    "shutter_speed",
    "aperture",
]


def _flatten_result(r: dict) -> dict:
    """Flatten a nested pipeline result dict into flat CSV columns.

    The analysis pipeline stores metrics under nested keys
    (``r["tier1"]["snr_sharpness"]``, ``r["exif"]["iso"]``, ...).  This
    helper projects them onto the flat ``CSV_COLUMNS`` schema.

    It is tolerant of dicts that are *already* flat (e.g. results
    reconstructed from a CSV via ``--from-csv``): an explicit top-level
    value always wins over the nested fallback.

    Args:
        r: A single result dict (nested or already-flat).

    Returns:
        A flat dict keyed by ``CSV_COLUMNS``.
    """
    exif = r.get("exif") or {}
    tier1 = r.get("tier1") or {}
    exposure = tier1.get("exposure") or {}
    tier2 = r.get("tier2") or {}

    def pick(col: str, fallback: Any) -> Any:
        """Prefer an explicit, non-empty top-level value, else the fallback."""
        if col in r and r[col] not in (None, ""):
            return r[col]
        return "" if fallback is None else fallback

    reject_tier = r.get("reject_tier")

    return {
        "filename": r.get("filename", ""),
        "tier1_snr_sharpness": pick("tier1_snr_sharpness", tier1.get("snr_sharpness")),
        "tier1_fft": pick("tier1_fft", tier1.get("fft_blur")),
        "tier1_exposure_shadows": pick("tier1_exposure_shadows", exposure.get("shadow_ratio")),
        "tier1_exposure_highlights": pick("tier1_exposure_highlights", exposure.get("highlight_ratio")),
        "tier2_persons_detected": pick("tier2_persons_detected", tier2.get("persons_detected")),
        "tier2_best_subject_snr": pick("tier2_best_subject_snr", tier2.get("best_subject_snr")),
        "tier2_subject_brightness": pick("tier2_subject_brightness", tier2.get("best_subject_brightness")),
        "has_ball": pick("has_ball", tier2.get("has_ball")),
        "crop_potential": pick("crop_potential", tier2.get("crop_potential")),
        "pose_face_visible": pick("pose_face_visible", tier2.get("pose_face_visible")),
        "pose_dynamic_stride": pick("pose_dynamic_stride", tier2.get("pose_dynamic_stride")),
        "face_eyes_closed": pick("face_eyes_closed", tier2.get("face_eyes_closed")),
        "face_bad_grimace": pick("face_bad_grimace", tier2.get("face_bad_grimace")),
        "final_score": r.get("final_score", ""),
        "decision": r.get("decision", ""),
        "reject_reason": r.get("reject_reason") or "",
        "reject_tier": reject_tier if reject_tier is not None else "",
        "iso": pick("iso", exif.get("iso")),
        "shutter_speed": pick("shutter_speed", exif.get("shutter_speed")),
        "aperture": pick("aperture", exif.get("aperture")),
    }


def generate_csv_report(results: list[dict], output_path: str) -> str:
    """Write analysis results to a CSV file.

    Creates a CSV report with one row per image and columns for every
    analysis metric, the final decision, and EXIF metadata.

    Args:
        results: List of result dicts from the analysis pipeline.
            Each dict should contain keys matching ``CSV_COLUMNS``.
            Missing keys are written as empty strings.
        output_path: Absolute or relative path for the output CSV file.
            Parent directories are created automatically.

    Returns:
        The absolute path of the written CSV file.

    Raises:
        OSError: If the file cannot be written (permissions, disk full, etc.).
        ValueError: If *results* is empty.
    """
    if not results:
        raise ValueError("Cannot generate CSV report from empty results list.")

    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    flat_rows = [_flatten_result(r) for r in results]

    def _write(target: Path) -> None:
        with open(target, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(flat_rows)

    try:
        _write(out)
    except PermissionError:
        # File is likely open in Excel/Lightroom — write to a timestamped copy.
        fallback_name = f"{out.stem}_{int(datetime.now().timestamp())}{out.suffix}"
        out = out.with_name(fallback_name)
        _write(out)

    return str(out)


def generate_json_report(
    results: list[dict],
    output_path: str,
    processing_time: float,
) -> str:
    """Write a comprehensive JSON report with results and statistics.

    The JSON structure contains:
    - ``metadata``: timestamp, version, processing time
    - ``summary``: total/passed/rejected counts, rejection breakdown,
      average processing time, score histograms
    - ``results``: the full per-image result array

    Args:
        results: List of result dicts from the analysis pipeline.
        output_path: Absolute or relative path for the output JSON file.
            Parent directories are created automatically.
        processing_time: Total wall-clock time in seconds for the run.

    Returns:
        The absolute path of the written JSON file.

    Raises:
        OSError: If the file cannot be written.
        ValueError: If *results* is empty.
    """
    if not results:
        raise ValueError("Cannot generate JSON report from empty results list.")

    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    summary = generate_summary_stats(results)
    summary["processing_time_seconds"] = round(processing_time, 3)
    summary["avg_processing_time_per_image"] = (
        round(processing_time / len(results), 4) if results else 0.0
    )

    report: dict[str, Any] = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "autocull_version": _get_version(),
            "total_processing_time_seconds": round(processing_time, 3),
        },
        "summary": summary,
        "results": results,
    }

    try:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
    except PermissionError:
        fallback_name = f"{out.stem}_{int(datetime.now().timestamp())}{out.suffix}"
        out = out.with_name(fallback_name)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)

    return str(out)


def generate_summary_stats(results: list[dict]) -> dict:
    """Calculate aggregate statistics from analysis results.

    Produces a dict suitable for both the JSON report and the Rich CLI
    dashboard display.

    Args:
        results: List of result dicts from the analysis pipeline.

    Returns:
        Dictionary containing:
        - ``total``, ``passed``, ``rejected``: counts
        - ``pass_rate``: percentage string (e.g. ``"72.5%"``)
        - ``rejection_breakdown``: ``{reason: count}`` mapping
        - ``tier_breakdown``: ``{tier: count}`` mapping
        - ``score_histogram``: 10-bin histogram of ``final_score``
        - ``sharpness_histogram``: 10-bin histogram of ``tier1_snr_sharpness``
        - ``score_stats``: min/max/mean/median for ``final_score``
        - ``sharpness_stats``: min/max/mean/median for ``tier1_snr_sharpness``
        - ``iso_distribution``: ``{iso: count}`` mapping
    """
    if not results:
        return {
            "total": 0,
            "passed": 0,
            "rejected": 0,
            "pass_rate": "0.0%",
            "rejection_breakdown": {},
            "tier_breakdown": {},
            "score_histogram": [],
            "sharpness_histogram": [],
            "score_stats": {},
            "sharpness_stats": {},
            "iso_distribution": {},
        }

    # Flatten nested pipeline dicts so metric columns (sharpness, iso, ...)
    # are accessible at the top level for histograms and distributions.
    results = [_flatten_result(r) for r in results]

    total = len(results)
    passed = sum(1 for r in results if r.get("decision") == "PASS")
    rejected = total - passed

    # Rejection reason breakdown
    rejection_reasons = [
        r.get("reject_reason", "unknown")
        for r in results
        if r.get("decision") == "REJECT"
    ]
    rejection_breakdown = dict(Counter(rejection_reasons))

    # Tier breakdown
    rejection_tiers = [
        str(r.get("reject_tier", "unknown"))
        for r in results
        if r.get("decision") == "REJECT"
    ]
    tier_breakdown = dict(Counter(rejection_tiers))

    # Score statistics & histogram
    scores = [
        float(r["final_score"])
        for r in results
        if r.get("final_score") not in (None, "")
    ]
    score_stats = _compute_stats(scores) if scores else {}
    score_histogram = _build_histogram(scores, bins=10) if scores else []

    # Sharpness statistics & histogram
    sharpness_values = [
        float(r["tier1_snr_sharpness"])
        for r in results
        if r.get("tier1_snr_sharpness") not in (None, "")
    ]
    sharpness_stats = _compute_stats(sharpness_values) if sharpness_values else {}
    sharpness_histogram = (
        _build_histogram(sharpness_values, bins=10) if sharpness_values else []
    )

    # ISO distribution
    iso_values = [
        str(r.get("iso", "unknown"))
        for r in results
        if r.get("iso") is not None
    ]
    iso_distribution = dict(Counter(iso_values))

    return {
        "total": total,
        "passed": passed,
        "rejected": rejected,
        "pass_rate": f"{(passed / total * 100):.1f}%",
        "rejection_breakdown": rejection_breakdown,
        "tier_breakdown": tier_breakdown,
        "score_histogram": score_histogram,
        "sharpness_histogram": sharpness_histogram,
        "score_stats": score_stats,
        "sharpness_stats": sharpness_stats,
        "iso_distribution": iso_distribution,
    }


# ── Private Helpers ──────────────────────────────────────────────────────


def _compute_stats(values: list[float]) -> dict[str, float]:
    """Return min, max, mean, and median for a list of numeric values.

    Args:
        values: Non-empty list of floats.

    Returns:
        Dict with keys ``min``, ``max``, ``mean``, ``median``.
    """
    if not values:
        return {}

    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mean = sum(sorted_vals) / n

    if n % 2 == 1:
        median = sorted_vals[n // 2]
    else:
        median = (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0

    return {
        "min": round(sorted_vals[0], 4),
        "max": round(sorted_vals[-1], 4),
        "mean": round(mean, 4),
        "median": round(median, 4),
    }


def _build_histogram(
    values: list[float],
    bins: int = 10,
) -> list[dict[str, Any]]:
    """Build a simple histogram from a list of numeric values.

    Each bin is represented as ``{"range": "lo–hi", "count": N}``.

    Args:
        values: Non-empty list of floats.
        bins: Number of equal-width bins (default 10).

    Returns:
        List of dicts, one per bin, sorted by range.
    """
    if not values:
        return []

    lo = min(values)
    hi = max(values)

    # Avoid zero-width bins when all values are identical
    if lo == hi:
        return [{"range": f"{lo:.2f}–{hi:.2f}", "count": len(values)}]

    bin_width = (hi - lo) / bins
    histogram: list[dict[str, Any]] = []

    for i in range(bins):
        bin_lo = lo + i * bin_width
        bin_hi = lo + (i + 1) * bin_width
        # Last bin is inclusive on both ends
        if i == bins - 1:
            count = sum(1 for v in values if bin_lo <= v <= bin_hi)
        else:
            count = sum(1 for v in values if bin_lo <= v < bin_hi)
        histogram.append({
            "range": f"{bin_lo:.2f}–{bin_hi:.2f}",
            "count": count,
        })

    return histogram


def _get_version() -> str:
    """Return the Autocull package version, or 'unknown' if unavailable."""
    try:
        from autocull import __version__
        return __version__
    except ImportError:
        return "unknown"


# ── Self-Test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile

    # Build synthetic test data
    test_results: list[dict] = [
        {
            "filename": "DSC00001.ARW",
            "tier1_laplacian": 45.2,
            "tier1_snr_sharpness": 12.8,
            "tier1_fft": 0.65,
            "tier1_exposure_shadows": 0.15,
            "tier1_exposure_highlights": 0.02,
            "tier2_persons_detected": 2,
            "tier2_best_subject_snr": 14.1,
            "tier2_subject_brightness": 85.0,
            "final_score": 0.78,
            "decision": "PASS",
            "reject_reason": "",
            "reject_tier": "",
            "iso": 6400,
            "shutter_speed": "1/800",
            "aperture": "f/1.8",
        },
        {
            "filename": "DSC00002.ARW",
            "tier1_laplacian": 18.3,
            "tier1_snr_sharpness": 4.1,
            "tier1_fft": 0.22,
            "tier1_exposure_shadows": 0.70,
            "tier1_exposure_highlights": 0.01,
            "tier2_persons_detected": 0,
            "tier2_best_subject_snr": 0.0,
            "tier2_subject_brightness": 20.0,
            "final_score": 0.15,
            "decision": "REJECT",
            "reject_reason": "underexposed",
            "reject_tier": "1",
            "iso": 10000,
            "shutter_speed": "1/500",
            "aperture": "f/1.8",
        },
        {
            "filename": "DSC00003.ARW",
            "tier1_laplacian": 30.5,
            "tier1_snr_sharpness": 8.0,
            "tier1_fft": 0.55,
            "tier1_exposure_shadows": 0.20,
            "tier1_exposure_highlights": 0.05,
            "tier2_persons_detected": 1,
            "tier2_best_subject_snr": 3.2,
            "tier2_subject_brightness": 60.0,
            "final_score": 0.35,
            "decision": "REJECT",
            "reject_reason": "subject_blurry",
            "reject_tier": "2",
            "iso": 6400,
            "shutter_speed": "1/640",
            "aperture": "f/1.8",
        },
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        # CSV
        csv_path = generate_csv_report(
            test_results, os.path.join(tmpdir, "report.csv")
        )
        print(f"[OK] CSV report written to: {csv_path}")
        with open(csv_path, encoding="utf-8") as f:
            print(f"     Rows (excl. header): {sum(1 for _ in f) - 1}")

        # JSON
        json_path = generate_json_report(
            test_results, os.path.join(tmpdir, "report.json"), processing_time=4.567
        )
        print(f"[OK] JSON report written to: {json_path}")
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        print(f"     Summary: {data['summary']['total']} total, "
              f"{data['summary']['passed']} passed, "
              f"{data['summary']['rejected']} rejected")

        # Summary stats
        stats = generate_summary_stats(test_results)
        print(f"[OK] Summary stats: pass_rate={stats['pass_rate']}, "
              f"rejection_breakdown={stats['rejection_breakdown']}")
        print(f"     Score stats: {stats['score_stats']}")

    print("\nAll reporter tests passed.")
