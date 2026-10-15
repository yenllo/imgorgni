#!/usr/bin/env python3
"""
Auditoría de solo lectura para el NAS.
Hace un único recorrido (single-pass os.walk) y genera 'reporte_nas.txt'.

100% lectura — no toca photos_v2.db ni mueve archivos.
"""

import sys
from pathlib import Path as _Path

# Este script vive en mantenimiento/ pero los modulos (db.py, utils_*) y los
# datos (photos_v2.db, _salidas_locales/) viven en la raiz del repo.
_ROOT = _Path(__file__).resolve().parent.parent
_HERE = _Path(__file__).resolve().parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import os
import sys
import shutil
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime

MAX_DEPTH_TREE = 3
CRITICAL_EXTS = {
    '.db', '.sqlite', '.sqlite3', '.lrcat', '.lrdata',
    '.json', '.yaml', '.yml', '.ini', '.conf',
    '.py', '.sh', '.ps1', '.bat', '.zip', '.tar', '.gz', '.7z'
}
IGNORE_DIRS = {'.git', '__pycache__', 'node_modules', '$RECYCLE.BIN', 'System Volume Information'}

# Carpetas que son exports/derivadas, no originales (ver reapuntar_rutas.py)
CARPETAS_DERIVADAS = {
    'Fotos_Organizadas_IA_v3',
    '_previews',
    'galeria',
    '_autocull_curador',
}


def format_size(size_bytes: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:6.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def audit_nas(root_path: Path, output_file: Path):
    if not root_path.exists():
        print(f"[ERROR] La ruta '{root_path}' no existe o no está montada.")
        sys.exit(1)

    print(f"Escaneando '{root_path}' (solo lectura, una sola pasada)...")

    dir_direct_files = defaultdict(int)
    dir_direct_size = defaultdict(int)
    dir_exts = defaultdict(Counter)
    all_dirs = set()
    empty_dirs = []
    critical_files = []
    global_exts = Counter()
    total_files = 0
    total_size = 0
    unreadable_paths = []
    derivadas_detectadas = []

    for current_root, dirs, files in os.walk(root_path, topdown=True, onerror=lambda e: unreadable_paths.append(str(e))):
        # Filtrar directorios de sistema o caché para no ensuciar el reporte
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

        rel_dir = Path(current_root).relative_to(root_path)
        all_dirs.add(rel_dir)

        # Detectar carpetas derivadas conocidas
        if rel_dir.name in CARPETAS_DERIVADAS and len(rel_dir.parts) == 1:
            derivadas_detectadas.append(str(rel_dir))

        if not dirs and not files and rel_dir != Path('.'):
            empty_dirs.append(str(rel_dir))

        for f in files:
            fpath = Path(current_root) / f
            ext = fpath.suffix.lower() or '<sin_ext>'
            try:
                fsize = fpath.stat().st_size
            except OSError:
                fsize = 0

            total_files += 1
            total_size += fsize
            global_exts[ext] += 1

            dir_direct_files[rel_dir] += 1
            dir_direct_size[rel_dir] += fsize

            # Acumular estadísticas hacia los padres hasta MAX_DEPTH_TREE
            parts = rel_dir.parts
            for depth in range(min(len(parts), MAX_DEPTH_TREE) + 1):
                anc = Path(*parts[:depth]) if depth > 0 else Path('.')
                dir_exts[anc][ext] += 1

            if ext in CRITICAL_EXTS or f.startswith('config') or 'db' in f.lower():
                if len(critical_files) < 200:
                    critical_files.append((str(rel_dir / f), format_size(fsize)))

    # Calcular totales recursivos por carpeta de abajo hacia arriba
    dir_rec_files = defaultdict(int, dir_direct_files)
    dir_rec_size = defaultdict(int, dir_direct_size)

    for d in sorted(all_dirs, key=lambda p: len(p.parts), reverse=True):
        if d != Path('.'):
            parent = d.parent
            dir_rec_files[parent] += dir_rec_files[d]
            dir_rec_size[parent] += dir_rec_size[d]

    # Construir el reporte
    lines = []
    lines.append("=" * 80)
    lines.append(f"REPORTE DE AUDITORÍA NAS - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Ruta raíz analizada: {root_path.resolve()}")
    lines.append("=" * 80)

    try:
        usage = shutil.disk_usage(root_path)
        lines.append("\n[1. ESTADO DEL VOLUMEN]")
        lines.append(f"  Capacidad Total : {format_size(usage.total)}")
        lines.append(f"  Espacio Usado   : {format_size(usage.used)} ({usage.used / usage.total * 100:.1f}%)")
        lines.append(f"  Espacio Libre   : {format_size(usage.free)}")
    except Exception as e:
        lines.append(f"\n[1. ESTADO DEL VOLUMEN] No disponible: {e}")

    lines.append(f"  Total Archivos  : {total_files:,}")
    lines.append(f"  Peso Escaneado  : {format_size(total_size)}")
    lines.append(f"  Total Carpetas  : {len(all_dirs):,} (Vacías: {len(empty_dirs)})")

    lines.append("\n[2. TOP 20 EXTENSIONES EN EL NAS]")
    for ext, count in global_exts.most_common(20):
        lines.append(f"  {ext:<12} : {count:>10,} archivos")

    lines.append(f"\n[3. ESTRUCTURA DE CARPETAS (Profundidad <= {MAX_DEPTH_TREE})]")
    lines.append(f"{'Ruta Relativa':<52} | {'Peso Total':>10} | {'Archivos':>9} | {'Prom/Arch':>9} | {'Top Extensiones'}")
    lines.append("-" * 120)

    sorted_tree = sorted(
        [d for d in all_dirs if len(d.parts) <= MAX_DEPTH_TREE],
        key=lambda p: p.parts
    )
    for d in sorted_tree:
        depth = len(d.parts)
        indent = "  " * (depth - 1) if depth > 0 else ""
        label = "." if d == Path('.') else f"{indent}{d.name}/"

        # Marcar derivadas conocidas
        if d.name in CARPETAS_DERIVADAS and len(d.parts) == 1:
            label += " [DERIVADA]"

        if len(label) > 50:
            label = label[:47] + "..."
        top_e = ", ".join(f"{k}({v})" for k, v in dir_exts[d].most_common(3))
        avg = format_size(dir_rec_size[d] // dir_rec_files[d]) if dir_rec_files[d] > 0 else "     N/A"
        lines.append(f"{label:<52} | {format_size(dir_rec_size[d]):>10} | {dir_rec_files[d]:>9,} | {avg:>9} | {top_e}")

    # Top 25 carpetas más pesadas de cualquier nivel
    lines.append("\n[4. TOP 25 CARPETAS CON MÁS ARCHIVOS DIRECTOS (CUALQUIER NIVEL)]")
    top_direct = sorted(dir_direct_files.items(), key=lambda x: x[1], reverse=True)[:25]
    for d, count in top_direct:
        lines.append(f"  {str(d):<60} | {format_size(dir_direct_size[d]):>10} | {count:>8,} archivos directos")

    lines.append("\n[5. ARCHIVOS CRÍTICOS DETECTADOS (DBs, Catálogos, Configs, Scripts, Zips)]")
    if critical_files:
        for cf_path, cf_size in sorted(critical_files):
            lines.append(f"  {cf_size:>10}  {cf_path}")
    else:
        lines.append("  Ninguno detectado.")

    # Sección de carpetas derivadas
    lines.append("\n[6. CARPETAS DERIVADAS DETECTADAS (exports, no originales)]")
    if derivadas_detectadas:
        for dd in sorted(derivadas_detectadas):
            rec_size = dir_rec_size[Path(dd)]
            rec_files = dir_rec_files[Path(dd)]
            lines.append(f"  {dd:<40} | {format_size(rec_size):>10} | {rec_files:>9,} archivos")
        lines.append("  ⚠ Estas carpetas son salidas del pipeline, NO material original.")
        lines.append("    No las incluyas en la migración como fuente de verdad.")
    else:
        lines.append("  Ninguna detectada.")

    if empty_dirs:
        lines.append(f"\n[7. MUESTRA DE CARPETAS VACÍAS (Mostrando hasta 30 de {len(empty_dirs)})]")
        for ed in sorted(empty_dirs)[:30]:
            lines.append(f"  {ed}")

    if unreadable_paths:
        lines.append("\n[8. ERRORES DE PERMISOS / LECTURA]")
        for err in unreadable_paths[:20]:
            lines.append(f"  {err}")

    report_text = "\n".join(lines)
    output_file.write_text(report_text, encoding="utf-8")
    print(f"\nListo. Reporte guardado en: {output_file.resolve()}")
    print(f"  Archivos escaneados: {total_files:,}")
    print(f"  Peso total: {format_size(total_size)}")
    print(f"  Carpetas: {len(all_dirs):,}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    out = Path("reporte_nas.txt")
    audit_nas(target, out)
