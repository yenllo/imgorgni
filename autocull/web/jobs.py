"""Background jobs — run the (slow) analysis pipeline off the request thread.

The pipeline loads AI models and processes hundreds of photos, so it must
not block HTTP requests. Each analysis runs in a worker thread; the client
polls :func:`get_job` for progress and, on completion, loads the saved
session state.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from typing import Optional

from autocull.config import AnalysisConfig
from autocull.pipeline import run_pipeline
from autocull.web import state as state_mod

# job_id -> job dict. In-memory only (cleared on server restart).
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _set(job_id: str, **changes) -> None:
    with _LOCK:
        _JOBS[job_id].update(changes)


def get_job(job_id: str) -> Optional[dict]:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def start_analysis(folder: str, threshold: float = 30.0,
                   sharpness_percentile: float = 15.0,
                   min_persons: int = 1, workers: int = 0) -> str:
    """Kick off analysis of *folder* in a background thread.

    Returns:
        The job id to poll with :func:`get_job`.
    """
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "state": "running",
            "folder": folder,
            "completed": 0,
            "total": 0,
            "session_id": None,
            "error": None,
            "started_at": time.time(),
            "elapsed": 0.0,
        }

    config = AnalysisConfig()
    config.final_reject_percentile = threshold
    config.sharpness_reject_percentile = sharpness_percentile
    config.min_persons = min_persons
    config.num_workers = workers
    config.__post_init__()

    config_snapshot = {
        "final_reject_percentile": threshold,
        "sharpness_reject_percentile": sharpness_percentile,
        "min_persons": min_persons,
    }

    def _run() -> None:
        t0 = time.time()
        try:
            def progress(completed: int, total: int) -> None:
                _set(job_id, completed=completed, total=total,
                     elapsed=round(time.time() - t0, 1))

            results = run_pipeline(folder, config, progress_callback=progress)
            if not results:
                _set(job_id, state="error", error="No supported images found.",
                     elapsed=round(time.time() - t0, 1))
                return

            records = state_mod.build_records(results, window=config.burst_window_seconds)

            # Serialize with any concurrent decision toggle so neither clobbers
            # the other; re-read overrides inside the lock to catch choices made
            # while this analysis was running.
            with state_mod.state_write_lock:
                overrides = state_mod.load_overrides(folder)  # preserve prior choices
                # Drop overrides for photos no longer present.
                names = {r["name"] for r in records}
                overrides = {k: v for k, v in overrides.items() if k in names}

                st = state_mod.make_state(folder, records, config_snapshot, overrides)
                state_mod.save_state(folder, st)

            _set(job_id, state="done", session_id=st["session_id"],
                 completed=len(records), total=len(records),
                 elapsed=round(time.time() - t0, 1))
        except Exception as exc:  # noqa: BLE001 — surface any failure to the client
            _set(job_id, state="error",
                 error=f"{exc}\n{traceback.format_exc()}",
                 elapsed=round(time.time() - t0, 1))

    threading.Thread(target=_run, name=f"autocull-job-{job_id}", daemon=True).start()
    return job_id
