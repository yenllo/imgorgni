"""Output — Report generation, XMP sidecars, and file management."""

from autocull.output.reporter import (
    generate_csv_report,
    generate_json_report,
    generate_summary_stats,
)
from autocull.output.xmp import (
    generate_xmp_sidecar,
    generate_xmp_batch,
)
from autocull.output.mover import (
    move_accepted_files,
    save_undo_log,
    undo_moves,
)

__all__ = [
    "generate_csv_report",
    "generate_json_report",
    "generate_summary_stats",
    "generate_xmp_sidecar",
    "generate_xmp_batch",
    "move_accepted_files",
    "save_undo_log",
    "undo_moves",
]
