"""FastAPI server — REST API + static UI for the Autocull review app.

All endpoints are non-destructive except ``/api/export`` (opt-in copy/XMP).
Designed for local single-user use (binds to 127.0.0.1).
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from autocull.web import jobs
from autocull.web import state as state_mod
from autocull.web.thumbs import get_thumbnail

STATIC_DIR = Path(__file__).parent / "static"


# ── Request models ──────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    folder: str
    threshold: float = 30.0
    sharpness_percentile: float = 15.0
    min_persons: int = 1
    workers: int = 0


class DecisionRequest(BaseModel):
    folder: str
    name: str
    decision: str  # "keep" | "reject" | "auto"


class BrowseRequest(BaseModel):
    folder: str


class ExportRequest(BaseModel):
    folder: str
    mode: str = "copy"  # "copy" | "xmp"


def _valid_folder(folder: str) -> str:
    abs_path = os.path.abspath(folder)
    if not os.path.isdir(abs_path):
        raise HTTPException(status_code=400, detail=f"No es una carpeta válida: {abs_path}")
    return abs_path


def create_app() -> FastAPI:
    app = FastAPI(title="Autocull Web", version="0.1.0")

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def index():
        idx = STATIC_DIR / "index.html"
        if not idx.is_file():
            return JSONResponse({"error": "UI not found"}, status_code=500)
        return FileResponse(str(idx))

    # ── Analysis ────────────────────────────────────────────────────────
    @app.post("/api/analyze")
    def analyze(req: AnalyzeRequest):
        folder = _valid_folder(req.folder)
        job_id = jobs.start_analysis(
            folder,
            threshold=req.threshold,
            sharpness_percentile=req.sharpness_percentile,
            min_persons=req.min_persons,
            workers=req.workers,
        )
        return {"job_id": job_id}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        job = jobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job no encontrado")
        return job

    # ── Session data ────────────────────────────────────────────────────
    def _with_decisions(st: dict) -> dict:
        """Recompute effective decisions + summary for the client."""
        overrides = st.get("overrides", {})
        for rec in st.get("records", []):
            rec["decision"] = state_mod.effective_decision(rec, overrides)
        st["summary"] = state_mod.summarize(st.get("records", []), overrides)
        return st

    @app.get("/api/session")
    def session(folder: str = Query(...)):
        folder = _valid_folder(folder)
        st = state_mod.load_state(folder)
        if st is None:
            raise HTTPException(status_code=404, detail="Esta carpeta no ha sido analizada todavía.")
        return _with_decisions(st)

    @app.post("/api/browse")
    def browse(req: BrowseRequest):
        """Open a folder in the viewer without running the analysis.

        Order: sesión web guardada → ``autocull_report.json`` de una corrida del
        CLI (scores y decisiones reales, gratis) → simple listado de la carpeta
        (todo ``NEW``, sin score). Mueve nada, nunca.
        """
        folder = _valid_folder(req.folder)
        with state_mod.state_write_lock:
            st = state_mod.load_state(folder)
            if st is None:
                records = state_mod.import_report(folder)
                source = "cli_report" if records else "listing"
                if not records:
                    records = state_mod.browse_records(folder)
                if not records:
                    raise HTTPException(status_code=404,
                                        detail="No hay fotos soportadas en esta carpeta.")
                st = state_mod.make_state(folder, records, {"imported_from": source})
                state_mod.save_state(folder, st)
        return _with_decisions(st)

    @app.get("/api/thumb")
    def thumb(folder: str = Query(...), name: str = Query(...), size: int = 480):
        folder = _valid_folder(folder)
        path = get_thumbnail(folder, name, size)
        if path is None:
            raise HTTPException(status_code=404, detail="Miniatura no disponible")
        return FileResponse(path, media_type="image/jpeg")

    # ── Decisions (non-destructive overrides) ───────────────────────────
    @app.post("/api/decision")
    def decision(req: DecisionRequest):
        folder = _valid_folder(req.folder)
        if req.decision not in ("keep", "reject", "auto"):
            raise HTTPException(status_code=400, detail="Decisión inválida")

        with state_mod.state_write_lock:
            st = state_mod.load_state(folder)
            if st is None:
                raise HTTPException(status_code=404, detail="Sesión no encontrada")
            overrides = st.setdefault("overrides", {})
            if req.decision == "auto":
                overrides.pop(req.name, None)
            else:
                overrides[req.name] = req.decision
            state_mod.save_state(folder, st)
            rec = next((r for r in st["records"] if r["name"] == req.name), None)
            eff = state_mod.effective_decision(rec, overrides) if rec else "REJECT"
            return {"name": req.name, "decision": eff,
                    "overridden": req.name in overrides,
                    "summary": st["summary"]}

    # ── Export (opt-in) ─────────────────────────────────────────────────
    @app.post("/api/export")
    def export(req: ExportRequest):
        folder = _valid_folder(req.folder)
        try:
            result = state_mod.export_selection(folder, mode=req.mode)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return result

    return app


app = create_app()
