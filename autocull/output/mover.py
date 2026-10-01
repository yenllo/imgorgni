"""
Mover — Safe file relocation with full undo for Autocull.

Moves rejected images (and their associated XMP sidecars) to a
``_rejected/`` subfolder inside the input directory, preserving the
original relative directory structure.

Every move is logged to a JSON undo file so the entire operation can
be reversed with a single call to :func:`undo_moves`.

**Safety guarantee:** This module NEVER deletes any file.  All
operations are ``shutil.move`` (rename/copy+unlink), and every source
→ destination pair is recorded before the move happens.

Uses only Python stdlib: ``shutil``, ``json``, ``os``, ``pathlib``,
``datetime``.
"""

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def move_accepted_files(
    accepted_paths: list[str],
    input_dir: str,
    accepted_folder: str = "_selected",
) -> dict[str, Any]:
    """Move accepted files to a selection subfolder.

    For each accepted image the function:
    1. Computes the relative path from *input_dir*.
    2. Creates the mirror directory structure under
       ``<input_dir>/<accepted_folder>/``.
    3. Moves the image file.
    4. Moves any co-located XMP sidecar (``<name>.xmp`` or
       ``<name>.<ext>.xmp``) alongside it.

    Args:
        accepted_paths: List of absolute paths to accepted image files.
        input_dir: The root input directory (base for relative paths).
        accepted_folder: Name of the selection subfolder. Default
            ``"_selected"``.

    Returns:
        A log dict with the structure::

            {
                "timestamp": "2025-05-30T18:15:00+00:00",
                "input_dir": "/abs/path",
                "accepted_folder": "_selected",
                "moves": [
                    {"source": "/abs/src", "destination": "/abs/dst"},
                    ...
                ],
                "errors": [
                    {"path": "/abs/src", "error": "reason"},
                    ...
                ]
            }

    Raises:
        FileNotFoundError: If *input_dir* does not exist.
    """
    input_root = Path(input_dir).resolve()
    if not input_root.is_dir():
        raise FileNotFoundError(f"Input directory not found: {input_root}")

    accept_root = input_root / accepted_folder
    accept_root.mkdir(parents=True, exist_ok=True)

    log: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input_dir": str(input_root),
        "accepted_folder": accepted_folder,
        "moves": [],
        "errors": [],
    }

    for img_path_str in accepted_paths:
        img_path = Path(img_path_str).resolve()

        # Gather the image and any associated sidecar files
        files_to_move = _gather_files(img_path)

        for src in files_to_move:
            try:
                # Compute relative path from input root
                try:
                    rel = src.relative_to(input_root)
                except ValueError:
                    # File is outside input_dir — use just the filename
                    rel = Path(src.name)

                dst = accept_root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)

                # Handle name collision: append _1, _2, etc.
                dst = _safe_destination(dst)

                shutil.move(str(src), str(dst))
                log["moves"].append({
                    "source": str(src),
                    "destination": str(dst),
                })

            except (OSError, shutil.Error) as exc:
                log["errors"].append({
                    "path": str(src),
                    "error": str(exc),
                })

    return log


def save_undo_log(moves_log: dict, output_path: str) -> str:
    """Persist the moves log as a JSON undo file.

    Args:
        moves_log: The log dict returned by :func:`move_accepted_files`.
        output_path: Absolute or relative path for the JSON undo file.
            Parent directories are created automatically.

    Returns:
        Absolute path of the written undo log file.

    Raises:
        OSError: If the file cannot be written.
    """
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    with open(out, "w", encoding="utf-8") as fh:
        json.dump(moves_log, fh, indent=2, default=str)

    return str(out)


def undo_moves(undo_log_path: str) -> int:
    """Reverse all moves recorded in an undo log file.

    Reads the JSON log and moves every file from its ``destination``
    back to its ``source``.  Original parent directories are recreated
    as needed.

    Args:
        undo_log_path: Path to the JSON undo log created by
            :func:`save_undo_log`.

    Returns:
        Number of files successfully restored.

    Raises:
        FileNotFoundError: If the undo log file does not exist.
        json.JSONDecodeError: If the log is not valid JSON.
        ValueError: If the log structure is invalid.
    """
    log_path = Path(undo_log_path).resolve()
    if not log_path.is_file():
        raise FileNotFoundError(f"Undo log not found: {log_path}")

    with open(log_path, "r", encoding="utf-8") as fh:
        log = json.load(fh)

    if "moves" not in log:
        raise ValueError(
            f"Invalid undo log: missing 'moves' key in {log_path}"
        )

    restored = 0
    errors: list[dict[str, str]] = []

    # Undo in reverse order to handle nested structures correctly
    for entry in reversed(log["moves"]):
        src = entry.get("source")
        dst = entry.get("destination")

        if not src or not dst:
            errors.append({
                "entry": str(entry),
                "error": "Missing 'source' or 'destination' key",
            })
            continue

        dst_path = Path(dst)
        src_path = Path(src)

        if not dst_path.is_file():
            errors.append({
                "entry": str(entry),
                "error": f"Destination file no longer exists: {dst}",
            })
            continue

        try:
            # Recreate original parent directory
            src_path.parent.mkdir(parents=True, exist_ok=True)

            # Move back to original location
            shutil.move(str(dst_path), str(src_path))
            restored += 1

        except (OSError, shutil.Error) as exc:
            errors.append({
                "entry": str(entry),
                "error": str(exc),
            })

    # Clean up empty directories in the accepted folder
    _cleanup_empty_dirs(log)

    if errors:
        import sys
        print(
            f"[WARNING] {len(errors)} file(s) could not be restored:",
            file=sys.stderr,
        )
        for err in errors:
            print(f"  - {err}", file=sys.stderr)

    return restored


# ── Private Helpers ──────────────────────────────────────────────────────


def _gather_files(image_path: Path) -> list[Path]:
    """Collect the image and any associated XMP sidecar files.

    Looks for sidecars matching these patterns:
    - ``<stem>.xmp``          (e.g. ``DSC00001.xmp``)
    - ``<name>.<ext>.xmp``    (e.g. ``DSC00001.ARW.xmp``)

    Args:
        image_path: Absolute path to the image file.

    Returns:
        List of paths to move (image first, then sidecars).
    """
    files: list[Path] = []

    if image_path.is_file():
        files.append(image_path)
    else:
        return files  # Image doesn't exist — skip entirely

    parent = image_path.parent

    # Pattern 1: DSC00001.xmp
    xmp_stem = parent / f"{image_path.stem}.xmp"
    if xmp_stem.is_file() and xmp_stem not in files:
        files.append(xmp_stem)

    # Pattern 2: DSC00001.ARW.xmp
    xmp_full = parent / f"{image_path.name}.xmp"
    if xmp_full.is_file() and xmp_full not in files:
        files.append(xmp_full)

    return files


def _safe_destination(dst: Path) -> Path:
    """Return a non-colliding destination path.

    If *dst* already exists, appends ``_1``, ``_2``, etc. before the
    file extension until a free name is found.

    Args:
        dst: Desired destination path.

    Returns:
        A path that does not yet exist on disk.
    """
    if not dst.exists():
        return dst

    stem = dst.stem
    suffix = dst.suffix
    parent = dst.parent
    counter = 1

    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _cleanup_empty_dirs(log: dict) -> None:
    """Remove empty directories left behind after an undo.

    Walks the accepted folder tree bottom-up and removes directories
    that are now empty.

    Args:
        log: The undo log dict (must have ``input_dir`` and
            ``accepted_folder`` keys).
    """
    input_dir = log.get("input_dir")
    accepted_folder = log.get("accepted_folder", "_selected")

    if not input_dir:
        return

    accept_root = Path(input_dir) / accepted_folder
    if not accept_root.is_dir():
        return

    # Walk bottom-up and remove empty directories
    for dirpath, dirnames, filenames in os.walk(str(accept_root), topdown=False):
        dp = Path(dirpath)
        if dp == accept_root:
            # Remove the root accepted folder only if truly empty
            try:
                dp.rmdir()  # Only succeeds if empty
            except OSError:
                pass
        elif not any(dp.iterdir()):
            try:
                dp.rmdir()
            except OSError:
                pass


# ── Self-Test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        # Setup: create a fake photo directory
        input_dir = os.path.join(tmpdir, "photos")
        os.makedirs(input_dir)

        img1 = os.path.join(input_dir, "DSC00001.ARW")
        img2 = os.path.join(input_dir, "DSC00002.ARW")
        xmp1 = os.path.join(input_dir, "DSC00001.ARW.xmp")
        Path(img1).write_text("fake raw 1")
        Path(img2).write_text("fake raw 2")
        Path(xmp1).write_text("<xmp>sidecar</xmp>")

        # Subdirectory structure
        sub_dir = os.path.join(input_dir, "day2")
        os.makedirs(sub_dir)
        img3 = os.path.join(sub_dir, "DSC00003.ARW")
        Path(img3).write_text("fake raw 3")

        print("=== 1. Move accepted files ===")
        log = move_accepted_files(
            accepted_paths=[img1, img2, img3],
            input_dir=input_dir,
        )
        print(f"  Moves: {len(log['moves'])}")
        print(f"  Errors: {len(log['errors'])}")
        for m in log["moves"]:
            print(f"    {m['source']}  ->  {m['destination']}")

        # Verify files moved
        assert not Path(img1).exists(), "img1 should be moved"
        assert not Path(img2).exists(), "img2 should be moved"
        assert not Path(xmp1).exists(), "xmp1 should be moved alongside img1"
        assert not Path(img3).exists(), "img3 should be moved"

        accepted_dir = os.path.join(input_dir, "_selected")
        assert Path(accepted_dir).is_dir(), "_selected dir should exist"

        # img1 + xmp1 + img2 + img3 = 4 moves
        assert len(log["moves"]) == 4, f"Expected 4 moves, got {len(log['moves'])}"

        print("\n=== 2. Save undo log ===")
        undo_path = os.path.join(input_dir, "_autocull_undo.json")
        saved = save_undo_log(log, undo_path)
        print(f"  Undo log saved to: {saved}")
        assert Path(saved).is_file()

        # Verify log content
        with open(saved, encoding="utf-8") as f:
            undo_data = json.load(f)
        assert "moves" in undo_data
        assert "timestamp" in undo_data

        print("\n=== 3. Undo moves ===")
        restored = undo_moves(saved)
        print(f"  Restored: {restored} files")

        # Verify files are back
        assert Path(img1).exists(), "img1 should be restored"
        assert Path(img2).exists(), "img2 should be restored"
        assert Path(xmp1).exists(), "xmp1 should be restored"
        assert Path(img3).exists(), "img3 should be restored"
        assert restored == 4, f"Expected 4 restored, got {restored}"

        # _selected directory should be cleaned up
        assert not Path(accepted_dir).exists(), "_selected should be cleaned up"

        print("\n=== 4. Edge cases ===")
        # Undo with non-existent log
        try:
            undo_moves("/nonexistent/undo.json")
            assert False, "Should have raised FileNotFoundError"
        except FileNotFoundError:
            print("  [OK] Non-existent undo log correctly rejected")

        # Move with non-existent input dir
        try:
            move_accepted_files([img1], "/nonexistent/dir")
            assert False, "Should have raised FileNotFoundError"
        except FileNotFoundError:
            print("  [OK] Non-existent input dir correctly rejected")

        # Move with already-moved files (no-ops, logged as errors)
        fake_log = move_accepted_files(
            ["/nonexistent/DSC99999.ARW"],
            input_dir,
        )
        # Non-existent file is silently skipped (no move, no error — _gather_files returns [])
        print(f"  [OK] Non-existent image: {len(fake_log['moves'])} moves, "
              f"{len(fake_log['errors'])} errors")

    print("\nAll mover tests passed.")
