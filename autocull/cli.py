"""
Command Line Interface for Autocull using Rich for beautiful terminal output.
"""

import os
import argparse
import time
import csv

# VERY IMPORTANT: Limit NumPy/OpenBLAS to 1 thread per worker.
# Otherwise, 16 workers * 16 threads = 256 threads thrashing the CPU,
# causing np.fft.fft2 to hang completely.
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["OPENCV_NUM_THREADS"] = "1"

# Suppress MediaPipe/GLOG C++ warnings
os.environ["GLOG_minloglevel"] = "2"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"

import numpy as np
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

from autocull.config import AnalysisConfig
from autocull.extractors.preview import find_images
from autocull.pipeline import run_pipeline
from autocull.output.reporter import generate_csv_report, generate_json_report, generate_summary_stats, CSV_COLUMNS
from autocull.output.xmp import generate_xmp_batch
from autocull.output.mover import move_accepted_files, save_undo_log, undo_moves

def main():
    parser = argparse.ArgumentParser(description="Autocull — Technical Photo Culling for Night Sports")
    parser.add_argument("input_dir", type=str, nargs="?", help="Directory containing images")
    parser.add_argument("--dry-run", action="store_true", help="Only analyze, do not move files or write XMP")
    parser.add_argument("--threshold", type=float, default=30.0, help="Percentile threshold for rejection (default: 30)")
    parser.add_argument("--sharpness-percentile", type=float, default=15.0, help="Tier 1 sharpness reject percentile")
    parser.add_argument("--min-persons", type=int, default=1, help="Minimum persons required for PASS (default: 1)")
    parser.add_argument("--workers", type=int, default=0, help="Number of parallel workers (0 = auto)")
    parser.add_argument("--backend", type=str, choices=["auto", "cpu", "directml", "npu"], default="auto", help="YOLO backend")
    parser.add_argument("--xmp", action="store_true", help="Generate XMP sidecars for Lightroom")
    parser.add_argument("--undo", action="store_true", help="Reverse the last move operation")
    parser.add_argument("--from-csv", type=str, help="Path to a previous autocull_report.csv to recalculate threshold instantly")
    
    args = parser.parse_args()
    console = Console()

    # Handle Undo
    if args.undo:
        if not args.input_dir:
            console.print("[red]Error: Must specify input_dir for --undo[/red]")
            return
            
        undo_log = os.path.join(args.input_dir, "_autocull_undo.json")
        if not os.path.exists(undo_log):
            console.print(f"[red]Error: Undo log not found at {undo_log}[/red]")
            return
            
        console.print("[yellow]Reversing previous Autocull moves...[/yellow]")
        count = undo_moves(undo_log)
        console.print(f"[green]Successfully restored {count} files.[/green]")
        return

    if not args.input_dir:
        parser.print_help()
        return

    input_dir = os.path.abspath(args.input_dir)
    if not os.path.exists(input_dir):
        console.print(f"[red]Error: Directory not found: {input_dir}[/red]")
        return

    # Configuration
    config = AnalysisConfig()
    config.final_reject_percentile = args.threshold
    config.sharpness_reject_percentile = args.sharpness_percentile
    config.min_persons = args.min_persons
    config.num_workers = args.workers
    config.yolo_backend = args.backend
    config.__post_init__()

    console.print(Panel.fit("🏟️ Autocull v1.0 — Night Sports Culling", style="bold blue"))

    # 1. Scanning
    try:
        files, fmt = find_images(input_dir, config)
    except Exception as e:
        console.print(f"[red]{e}[/red]")
        return

    console.print(f"📂 Scanning: [cyan]{input_dir}[/cyan]")
    console.print(f"   Found [bold]{len(files)}[/bold] {fmt.upper()} files.")
    
    if len(files) == 0:
        return

    # 2. Pipeline Execution OR Load from CSV
    results = []
    t0 = time.time()
    
    if args.from_csv:
        csv_file = os.path.abspath(args.from_csv)
        if not os.path.exists(csv_file):
            console.print(f"[red]Error: CSV file not found: {csv_file}[/red]")
            return
            
        console.print(f"[yellow]Loading previous results from {csv_file}...[/yellow]")
        with open(csv_file, "r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                # Trust the recorded decision/reason/tier — reconstructing them
                # from reject_tier alone loses tier-5 (burst) rejections.
                try:
                    f_score = float(row.get("final_score") or 0.0)
                except ValueError:
                    f_score = 0.0

                decision = (row.get("decision") or "").strip().upper() or "REJECT"
                rtier_str = str(row.get("reject_tier", "")).strip()

                parsed = {
                    "filename": row.get("filename", ""),
                    "filepath": os.path.join(input_dir, row.get("filename", "")),
                    "final_score": f_score,
                    "decision": decision,
                    "reject_reason": (row.get("reject_reason") or None) if decision == "REJECT" else None,
                    "reject_tier": int(rtier_str) if rtier_str.isdigit() else None,
                    "iso": int(float(row.get("iso") or 0)),
                    "exif": {},
                }

                # Copy other stats for histograms
                for col in CSV_COLUMNS:
                    if col not in parsed and col in row:
                        parsed[col] = row[col]

                results.append(parsed)

        # Re-apply ONLY the final percentile cut. Eligible = photos that cleared
        # every hard filter and burst dedup: the current PASS set plus anything
        # previously cut *only* by the final percentile (so a lower threshold can
        # bring them back). Tier 1/2/5 rejects stay rejected.
        def _final_cut_eligible(r):
            return r["decision"] == "PASS" or r.get("reject_reason") == "below_final_percentile"

        eligible = [r for r in results if _final_cut_eligible(r)]
        pass_scores = [r["final_score"] for r in eligible]
        if pass_scores:
            final_threshold = np.percentile(pass_scores, config.final_reject_percentile)
            for r in eligible:
                if r["final_score"] < final_threshold:
                    r["decision"] = "REJECT"
                    r["reject_reason"] = "below_final_percentile"
                    r["reject_tier"] = 3
                else:
                    r["decision"] = "PASS"
                    r["reject_reason"] = None
                    r["reject_tier"] = None
                        
    else:
        console.print(f"🧠 Loading AI models into {config.num_workers} workers... (this takes ~10-15s)")
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total})"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
        ) as progress:
            task_id = progress.add_task("[cyan]Analyzing photos (Tier 1 & Tier 2)...", total=len(files))
            
            def update_progress(completed, total):
                progress.update(task_id, completed=completed)
                
            results = run_pipeline(input_dir, config, progress_callback=update_progress)
            
    processing_time = time.time() - t0

    # 3. Output Generation
    if not args.dry_run:
        with console.status("[yellow]Generating outputs...[/yellow]"):
            accepted_results = [r for r in results if r.get("decision") == "PASS"]
            accepted_paths = [r["filepath"] for r in accepted_results]
            
            # XMP (Still write reject tags to ALL files)
            if args.xmp:
                rejected_paths = [r["filepath"] for r in results if r.get("decision") == "REJECT"]
                if rejected_paths:
                    generate_xmp_batch(rejected_paths, rating=config.xmp_reject_rating, label=config.xmp_reject_label)
                
            # Reports
            csv_path = os.path.join(input_dir, config.report_csv_filename)
            json_path = os.path.join(input_dir, config.report_json_filename)
            csv_path = generate_csv_report(results, csv_path)
            json_path = generate_json_report(results, json_path, processing_time)
            
            # Move files
            if accepted_paths:
                moves_log = move_accepted_files(accepted_paths, input_dir, config.accepted_folder)
                save_undo_log(moves_log, os.path.join(input_dir, config.undo_log_filename))
    else:
        # Just generate reports in dry run
        csv_path = os.path.join(input_dir, config.report_csv_filename)
        csv_path = generate_csv_report(results, csv_path)
        console.print("[yellow]DRY RUN: Files were not moved. Reports generated.[/yellow]")

    # 4. Summary Display
    stats = generate_summary_stats(results)
    
    table = Table(title="⚽ RESULTS SUMMARY", show_header=False, box=None)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold")
    
    table.add_row("Total analyzed", str(stats["total"]))
    pass_pct = (stats['passed'] / stats['total'] * 100) if stats['total'] else 0
    reject_pct = 100 - pass_pct
    
    table.add_row("✅ PASS", f"{stats['passed']} ({pass_pct:.1f}%)")
    table.add_row("❌ REJECT", f"{stats['rejected']} ({reject_pct:.1f}%)")
    
    # Tier breakdowns
    table.add_row("", "")
    table.add_row("Tier 1 Rejects", str(stats["tier_breakdown"].get("1", 0)))
    for reason, count in stats["rejection_breakdown"].items():
        if reason in ["severely_underexposed", "blown_highlights", "global_blur_snr", "motion_blur_fft"]:
            pct = count / stats['total'] * 100 if stats['total'] else 0
            table.add_row(f"  ├─ {reason}", f"{count} ({pct:.1f}%)")
            
    table.add_row("Tier 2 Rejects", str(stats["tier_breakdown"].get("2", 0)))
    for reason, count in stats["rejection_breakdown"].items():
        if reason in ["no_subject", "subject_out_of_focus", "subject_too_dark"]:
            pct = count / stats['total'] * 100 if stats['total'] else 0
            table.add_row(f"  ├─ {reason}", f"{count} ({pct:.1f}%)")
            
    table.add_row("Ráfaga (duplicados)", str(stats["tier_breakdown"].get("5", 0)))
    if "burst_duplicate" in stats["rejection_breakdown"]:
        count = stats["rejection_breakdown"]["burst_duplicate"]
        pct = count / stats['total'] * 100 if stats['total'] else 0
        table.add_row("  ├─ Mejor de ráfaga conservada", f"{count} ({pct:.1f}%)")

    table.add_row("Final Cut Rejects", str(stats["tier_breakdown"].get("3", 0)))
    if "below_final_percentile" in stats["rejection_breakdown"]:
        count = stats["rejection_breakdown"]["below_final_percentile"]
        pct = count / stats['total'] * 100 if stats['total'] else 0
        table.add_row(f"  ├─ Percentile cutoff", f"{count} ({pct:.1f}%)")
        
    table.add_row("", "")
    table.add_row("Processing time", f"{processing_time:.1f}s")
    
    console.print(Panel(table, border_style="blue"))
    
    console.print(f"📄 Report: [cyan]{csv_path}[/cyan]")
    if not args.dry_run and args.xmp:
        console.print(f"📝 XMP: [cyan]{stats['rejected']} sidecars written[/cyan]")
    if not args.dry_run:
        console.print(f"📁 Selected: [cyan]{os.path.join(input_dir, config.accepted_folder)}[/cyan]")


if __name__ == "__main__":
    main()
