"""Autocull Web — Local review app (FastAPI backend + single-page UI).

A non-destructive layer on top of the analysis pipeline: analyse a folder,
browse the results grouped by burst, override the keep/reject decision per
photo, and (optionally) export the selection. Nothing is moved or deleted
unless you explicitly export.

Run with::

    python -m autocull.web              # serves http://127.0.0.1:8000
"""

__all__ = ["create_app"]


def create_app():
    """Lazily build the FastAPI app (keeps web deps optional)."""
    from autocull.web.server import create_app as _create_app
    return _create_app()
