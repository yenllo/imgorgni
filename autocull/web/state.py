"""Session state — non-destructive persistence for the review app.

A *session* is one analysed folder. Its state lives next to the photos in
``<folder>/_autocull_session.json`` so it travels with them and never
touches the originals. The state holds the per-photo analysis records,
burst grouping for the review UI, and the user's keep/reject overrides.

Nothing here moves or deletes photos. Export (copy / XMP) is opt-in and
lives in :func:`export_selection`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from autocull.output.reporter import _flatten_result

STATE_FILENAME = "_autocull_session.json"
CACHE_DIRNAME = "_autocull_cache"
SELECTED_DIRNAME = "_selected"

# Serializes read-modify-write of the session state file across threads
# (decision toggles in the request thread vs. the background analysis job).
state_write_lock = threading.Lock()

# Default burst window for UI grouping; mirrors config.burst_window_seconds so
# the review UI groups photos the same way the culler's dedup does.
BURST_WINDOW_SECONDS = 1.5


def session_id_for(folder: str) -> str:
    """Stable short id derived from the absolute folder path."""
    abs_path = os.path.abspath(folder)
    return hashlib.sha1(abs_path.encode("utf-8")).hexdigest()[:12]


def state_path(folder: str) -> Path:
    return Path(folder) / STATE_FILENAME


# ── Record building & burst grouping ────────────────────────────────────────

def _timestamp_of(result: dict) -> float:
    """Best-effort capture time (epoch seconds) for chronological grouping.

    Prefers EXIF ``DateTimeOriginal``; falls back to file mtime. Used only
    for *display* grouping — never for rejection.
    """
    exif = result.get("exif") or {}
    dt = exif.get("datetime_obj")
    if isinstance(dt, datetime):
        return dt.timestamp()
    try:
        return os.path.getmtime(result["filepath"])
    except Exception:
        return 0.0


def build_records(results: list[dict], window: float = BURST_WINDOW_SECONDS) -> list[dict]:
    """Project nested pipeline results into compact UI records + burst groups.

    Args:
        results: Nested result dicts from :func:`autocull.pipeline.run_pipeline`.
        window: Burst grouping window in seconds (see config.burst_window_seconds).

    Returns:
        List of flat record dicts, each with a ``burst_group`` id, sorted
        chronologically.
    """
    records: list[dict] = []
    for r in results:
        flat = _flatten_result(r)
        exif = r.get("exif") or {}
        dt = exif.get("datetime_obj")
        records.append({
            "name": r.get("filename", ""),
            "score": _num(flat.get("final_score")),
            "auto_decision": flat.get("decision") or ("ERROR" if r.get("error") else ""),
            "reject_reason": flat.get("reject_reason") or "",
            "reject_tier": flat.get("reject_tier"),
            "iso": _num(flat.get("iso")),
            "persons": _num(flat.get("tier2_persons_detected")),
            "subj_snr": _num(flat.get("tier2_best_subject_snr")),
            "global_snr": _num(flat.get("tier1_snr_sharpness")),
            "brightness": _num(flat.get("tier2_subject_brightness")),
            "has_ball": _truthy(flat.get("has_ball")),
            "pose_face": _num(flat.get("pose_face_visible")),
            "pose_stride": _num(flat.get("pose_dynamic_stride")),
            "eyes_closed": _truthy(flat.get("face_eyes_closed")),
            "grimace": _truthy(flat.get("face_bad_grimace")),
            "datetime": dt.isoformat() if isinstance(dt, datetime) else "",
            "_ts": _timestamp_of(r),
            "error": r.get("error"),
        })

    # Chronological order, then assign burst groups by time gaps.
    records.sort(key=lambda rec: (rec["_ts"], rec["name"]))
    group = 0
    prev_ts: Optional[float] = None
    for rec in records:
        ts = rec["_ts"]
        if prev_ts is not None and (ts - prev_ts) > window:
            group += 1
        rec["burst_group"] = group
        prev_ts = ts
        del rec["_ts"]  # internal only

    return records


def browse_records(folder: str) -> list[dict]:
    """Records for a folder that was never analysed — just the photos on disk.

    Lets the viewer open any carpeta and keep/reject by hand without running
    the pipeline (and without moving anything). Scores stay 0 and the auto
    decision is ``"NEW"``, so nothing is judged.

    ponytail: burst grouping falls back to mtime here (no EXIF read), which is
    enough to keep ráfagas together in the grid; run the análisis if you want
    EXIF-accurate groups.
    """
    from autocull.config import DEFAULT_CONFIG
    from autocull.extractors.preview import find_images

    try:
        files, _fmt = find_images(folder, DEFAULT_CONFIG)
    except (FileNotFoundError, ValueError):
        files = []

    results = [{"filename": _rel_name(folder, p), "filepath": p, "decision": "NEW"}
               for p in files]
    return build_records(results)


def _rel_name(folder: str, filepath: str) -> str:
    """Record name = path relative to the session folder, '/'-separated.

    Sony rolls arrive split across ``100MSDCF/``, ``101MSDCF/``…, so a plain
    basename would be ambiguous and the thumbnail endpoint could not find the file.
    """
    try:
        rel = os.path.relpath(os.path.abspath(filepath), os.path.abspath(folder))
    except ValueError:  # different drive
        return os.path.basename(filepath)
    return rel.replace("\\", "/")


def import_report(folder: str) -> list[dict]:
    """Records from a CLI run's ``autocull_report.json`` — no re-analysis.

    The CLI writes the full nested results next to the photos; reusing them
    gives the viewer real scores and decisions for free (the roll of 5407 RAW
    took 34 min to analyse).

    Returns:
        Records, or ``[]`` if there is no readable report.
    """
    report = Path(folder) / "autocull_report.json"
    if not report.is_file():
        return []
    try:
        with open(report, "r", encoding="utf-8") as fh:
            results = (json.load(fh) or {}).get("results") or []
    except (OSError, json.JSONDecodeError):
        return []

    for r in results:
        r["filename"] = _rel_name(folder, r.get("filepath") or r.get("filename", ""))
        exif = r.get("exif") or {}
        dt = exif.get("datetime_obj")
        if isinstance(dt, str):  # JSON round-trip turned it into a string
            try:
                exif["datetime_obj"] = datetime.fromisoformat(dt)
            except ValueError:
                exif["datetime_obj"] = None
    return build_records(results)


def _num(v: Any) -> float:
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return 0.0


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes")


# ── Effective decision & summary ────────────────────────────────────────────

def effective_decision(record: dict, overrides: dict) -> str:
    """Return the active decision: a user override wins over the auto one.

    Returns one of ``"PASS"``, ``"REJECT"``, ``"ERROR"``.
    """
    ov = overrides.get(record["name"])
    if ov == "keep":
        return "PASS"
    if ov == "reject":
        return "REJECT"
    return record.get("auto_decision") or "REJECT"


def summarize(records: list[dict], overrides: dict) -> dict:
    """Counts for the dashboard, honoring user overrides."""
    total = len(records)
    passed = sum(1 for r in records if effective_decision(r, overrides) == "PASS")
    # Explicit REJECT count: in a browse-only session the rest are "NEW",
    # neither kept nor rejected.
    rejected = sum(1 for r in records if effective_decision(r, overrides) == "REJECT")
    errored = sum(1 for r in records if r.get("error"))
    overridden = sum(1 for r in records if r["name"] in overrides)
    bursts = len({r["burst_group"] for r in records if "burst_group" in r})
    return {
        "total": total,
        "passed": passed,
        "rejected": rejected,
        "errored": errored,
        "overridden": overridden,
        "bursts": bursts,
    }


# ── Persistence ─────────────────────────────────────────────────────────────

def make_state(folder: str, records: list[dict], config_snapshot: dict,
               overrides: Optional[dict] = None) -> dict:
    overrides = overrides or {}
    return {
        "schema": 1,
        "session_id": session_id_for(folder),
        "folder": os.path.abspath(folder),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": config_snapshot,
        "records": records,
        "overrides": overrides,
        "summary": summarize(records, overrides),
    }


def save_state(folder: str, state: dict) -> str:
    out = state_path(folder)
    out.parent.mkdir(parents=True, exist_ok=True)
    state["summary"] = summarize(state.get("records", []), state.get("overrides", {}))
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, default=str)
    return str(out)


def load_state(folder: str) -> Optional[dict]:
    p = state_path(folder)
    if not p.is_file():
        return None
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def load_overrides(folder: str) -> dict:
    """Carry over a previous session's overrides when re-analysing."""
    prev = load_state(folder)
    return prev.get("overrides", {}) if prev else {}


# ── Export (opt-in, still non-destructive: copies, never moves) ──────────────

def export_selection(folder: str, mode: str = "copy") -> dict:
    """Apply the current selection.

    Args:
        folder: The session folder.
        mode: ``"copy"`` copies kept photos into ``_selected/`` (originals
            stay put). ``"xmp"`` writes reject rating/label sidecars for the
            rejected photos (for Lightroom), touching no image pixels.

    Returns:
        Summary dict ``{"mode", "count", "errors"}``.
    """
    state = load_state(folder)
    if state is None:
        raise FileNotFoundError("No analysed session found for this folder.")

    records = state.get("records", [])
    overrides = state.get("overrides", {})
    kept = [r for r in records if effective_decision(r, overrides) == "PASS"]
    rejected = [r for r in records if effective_decision(r, overrides) == "REJECT"]

    errors: list[str] = []

    if mode == "copy":
        dst_root = Path(folder) / SELECTED_DIRNAME
        dst_root.mkdir(parents=True, exist_ok=True)
        count = 0
        for r in kept:
            src = Path(folder) / r["name"]
            if not src.is_file():
                errors.append(f"missing: {r['name']}")
                continue
            try:
                shutil.copy2(src, dst_root / src.name)
                count += 1
            except OSError as exc:
                errors.append(f"{r['name']}: {exc}")
        return {"mode": mode, "count": count, "errors": errors,
                "destination": str(dst_root)}

    if mode == "xmp":
        # Aprobadas 5★/Verde, rechazadas 1★/Rojo: en Lightroom se filtra por
        # rating o por etiqueta sin mover ni duplicar un solo ARW.
        from autocull.output.xmp import generate_xmp_batch
        picks = generate_xmp_batch([str(Path(folder) / r["name"]) for r in kept],
                                   rating=5, label="Green")
        rejects = generate_xmp_batch([str(Path(folder) / r["name"]) for r in rejected],
                                     rating=1, label="Red")
        return {"mode": mode, "count": len(picks) + len(rejects),
                "picks": len(picks), "rejects": len(rejects), "errors": errors}

    raise ValueError(f"Unknown export mode: {mode!r}")


if __name__ == "__main__":
    # Self-check: browse (sin análisis) → override manual → export copia, sin mover nada.
    import tempfile

    import cv2
    import numpy as np

    d = tempfile.mkdtemp()
    for i in range(3):
        cv2.imwrite(os.path.join(d, f"img{i}.jpg"), np.full((60, 90, 3), 40 * i + 20, np.uint8))
    os.makedirs(os.path.join(d, "sub"))
    cv2.imwrite(os.path.join(d, "sub", "nope.jpg"), np.zeros((10, 10, 3), np.uint8))

    recs = browse_records(d)
    names = [r["name"] for r in recs]
    assert sorted(names) == ["img0.jpg", "img1.jpg", "img2.jpg", "sub/nope.jpg"], names
    assert all(r["auto_decision"] == "NEW" and r["score"] == 0 for r in recs)
    from autocull.web.thumbs import get_thumbnail
    assert get_thumbnail(d, "sub/nope.jpg"), "thumbnail de subcarpeta"
    assert get_thumbnail(d, "../escape.jpg") is None, "no debe salir de la carpeta"

    # Un reporte del CLI se importa con sus scores y decisiones reales.
    json.dump({"results": [
        {"filename": "img1.jpg", "filepath": os.path.join(d, "img1.jpg"),
         "final_score": 42.0, "decision": "PASS",
         "exif": {"iso": 8000, "datetime_obj": "2026-08-23 20:24:39"}},
    ]}, open(os.path.join(d, "autocull_report.json"), "w", encoding="utf-8"))
    imported = import_report(d)
    assert [(r["name"], r["score"], r["auto_decision"], r["iso"]) for r in imported] ==         [("img1.jpg", 42.0, "PASS", 8000.0)], imported
    assert imported[0]["datetime"].startswith("2026-08-23T20:24:39"), imported[0]["datetime"]

    recs = [r for r in recs if "/" not in r["name"]]
    st = make_state(d, recs, {"browse_only": True})
    save_state(d, st)
    assert st["summary"] == {"total": 3, "passed": 0, "rejected": 0, "errored": 0,
                             "overridden": 0, "bursts": 1}, st["summary"]

    st["overrides"]["img1.jpg"] = "keep"
    save_state(d, st)
    assert effective_decision(recs[1], st["overrides"]) == "PASS"
    assert load_state(d)["summary"]["passed"] == 1

    ex = export_selection(d, "copy")
    assert ex["count"] == 1 and os.listdir(os.path.join(d, SELECTED_DIRNAME)) == ["img1.jpg"], ex
    assert sorted(f for f in os.listdir(d) if f.endswith(".jpg")) == \
        ["img0.jpg", "img1.jpg", "img2.jpg"]  # originales intactos

    ex = export_selection(d, "xmp")
    assert (ex["picks"], ex["rejects"]) == (1, 0), ex  # solo img1 está en keep
    side = os.path.join(d, "img1.xmp")                 # convención Adobe, no img1.jpg.xmp
    assert os.path.isfile(side), os.listdir(d)
    body = open(side, encoding="utf-8").read()
    assert 'xmp:Rating="5"' in body and 'xmp:Label="Green"' in body, body

    assert browse_records(tempfile.mkdtemp()) == []
    print("state.py self-check OK:", d)
