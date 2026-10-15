#!/usr/bin/env python3
"""
Fase 2: Inspección rápida de _db y detección de Hardlinks vs Copias Físicas en K:\
100% Solo lectura. Toma menos de 10 segundos.
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
import sqlite3
from pathlib import Path
from collections import Counter

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("K:/")
OUT = Path("reporte_fase2.txt")


def format_size(b: int) -> str:
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024:
            return f"{b:6.2f} {u}"
        b /= 1024
    return f"{b:.2f} PB"


def inspect_dbs(lines):
    lines.append("=" * 80)
    lines.append("[1. CONTENIDO DE K:\\_db Y RUTAS INDEXADAS EN SQLITE]")
    lines.append("=" * 80)
    db_dir = ROOT / "_db"
    if not db_dir.exists():
        lines.append("  No existe K:\\_db")
        return

    for f in sorted(db_dir.iterdir()):
        if f.is_file():
            lines.append(f"  Archivo: {f.name:<55} | Peso: {format_size(f.stat().st_size)}")

    for db_file in sorted(db_dir.glob("*.db")):
        lines.append(f"\n  --- Analizando base de datos: {db_file.name} ---")
        try:
            conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
            cur = conn.cursor()
            tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            lines.append(f"  Tablas: {', '.join(tables)}")

            for t in tables:
                try:
                    cnt = cur.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
                    cols = [c[1] for c in cur.execute(f"PRAGMA table_info([{t}])").fetchall()]
                    lines.append(f"    - Tabla '{t}': {cnt:,} filas | Columnas: {', '.join(cols[:10])}")

                    # Buscar columnas que almacenen rutas
                    path_cols = [c for c in cols if any(k in c.lower() for k in ("ruta", "path", "archivo", "file", "origen", "destino"))]
                    for pc in path_cols[:2]:
                        sample = cur.execute(f"SELECT [{pc}] FROM [{t}] WHERE [{pc}] IS NOT NULL LIMIT 50000").fetchall()
                        top_roots = Counter()
                        for (val,) in sample:
                            if not val:
                                continue
                            p_str = str(val).replace("\\", "/")
                            # Extraer los primeros 2 segmentos de la ruta
                            parts = [x for x in p_str.split("/") if x and not x.endswith(":")]
                            prefix = "/".join(parts[:2]) if len(parts) >= 2 else (parts[0] if parts else "?")
                            top_roots[prefix] += 1
                        if top_roots:
                            lines.append(f"      Prefijos más comunes en '{t}.{pc}' (muestra hasta 50k):")
                            for pref, c in top_roots.most_common(12):
                                lines.append(f"        {pref:<45} : {c:>8,}")
                except Exception as e:
                    lines.append(f"    - Error leyendo tabla {t}: {e}")
            conn.close()
        except Exception as e:
            lines.append(f"  Error abriendo {db_file.name}: {e}")


def check_hardlinks_and_clones(lines):
    lines.append("\n" + "=" * 80)
    lines.append("[2. ANÁLISIS DE HARDLINKS (st_nlink > 1) POR CARPETA RAÍZ]")
    lines.append("=" * 80)

    suspect_dirs = [
        "DCIM Jpeg 2026",
        "SD_Jpeg_2026",
        "Fotos/Galeria",
        "Fotos/AOrdenar",
        "Fotos/_Telefono",
        "Fotos_Organizadas",
        "Fotos_Organizadas_IA_v3",
        "Lightroom_extraido",
        "_facet_train",
        "_duplicados_contenido",
        "_duplicados_cuarentena",
    ]

    inodes_by_dir = {}
    for dname in suspect_dirs:
        dpath = ROOT / dname
        if not dpath.exists():
            lines.append(f"  {dname:<28} | NO EXISTE")
            continue
        total_f = 0
        hardlinked_f = 0
        hardlinked_bytes = 0
        inodes = set()

        for r, _, files in os.walk(dpath):
            for f in files:
                fp = Path(r) / f
                try:
                    st = fp.stat()
                    total_f += 1
                    if st.st_ino:
                        inodes.add(st.st_ino)
                    if st.st_nlink > 1:
                        hardlinked_f += 1
                        hardlinked_bytes += st.st_size
                except OSError:
                    pass
        inodes_by_dir[dname] = inodes
        pct = (hardlinked_f / total_f * 100) if total_f else 0
        lines.append(
            f"  {dname:<28} | Archivos: {total_f:>8,} | Con Hardlink (nlink>1): {hardlinked_f:>8,} ({pct:5.1f}%) | Peso linkeado: {format_size(hardlinked_bytes)}"
        )

    lines.append("\n" + "=" * 80)
    lines.append("[3. CRUCE DE INODOS ENTRE CARPETAS SOSPECHOSAS (MISMO ARCHIVO FÍSICO)]")
    lines.append("=" * 80)
    pairs = [
        ("DCIM Jpeg 2026", "SD_Jpeg_2026"),
        ("Fotos_Organizadas_IA_v3", "Fotos/Galeria"),
        ("Fotos_Organizadas_IA_v3", "Lightroom_extraido"),
        ("Fotos_Organizadas_IA_v3", "Fotos/_Telefono"),
        ("_facet_train", "Fotos/Galeria"),
        ("Lightroom_extraido", "Fotos/Galeria"),
        ("Fotos_Organizadas", "Fotos/Galeria"),
    ]
    for a, b in pairs:
        if a in inodes_by_dir and b in inodes_by_dir:
            shared = len(inodes_by_dir[a] & inodes_by_dir[b])
            lines.append(f"  {a:<26} <-> {b:<22} : {shared:>8,} archivos comparten el mismo inodo físico")

    # Comparación directa por ruta relativa entre DCIM Jpeg 2026 y SD_Jpeg_2026
    dcim = ROOT / "DCIM Jpeg 2026"
    sd = ROOT / "SD_Jpeg_2026"
    if dcim.exists() and sd.exists():
        lines.append(f"\n  Construyendo mapa de DCIM Jpeg 2026...")
        dcim_map = {}
        for r, _, files in os.walk(dcim):
            for f in files:
                fp = Path(r) / f
                try:
                    dcim_map[Path(r).relative_to(dcim) / f] = fp.stat().st_size
                except OSError:
                    pass
        sd_map = {}
        for r, _, files in os.walk(sd):
            for f in files:
                fp = Path(r) / f
                try:
                    sd_map[Path(r).relative_to(sd) / f] = fp.stat().st_size
                except OSError:
                    pass
        same_rel_and_size = sum(1 for k, v in dcim_map.items() if sd_map.get(k) == v)
        lines.append(f"\n  Verificación DCIM Jpeg 2026 vs SD_Jpeg_2026:")
        lines.append(f"    Archivos con idéntica ruta relativa y tamaño exacto: {same_rel_and_size:,} de {len(dcim_map):,}")
        only_dcim = len(set(dcim_map) - set(sd_map))
        only_sd = len(set(sd_map) - set(dcim_map))
        if only_dcim or only_sd:
            lines.append(f"    Solo en DCIM: {only_dcim:,} | Solo en SD: {only_sd:,}")


if __name__ == "__main__":
    print(f"Analizando {ROOT}...")
    out_lines = []
    inspect_dbs(out_lines)
    print("  DBs listas. Escaneando hardlinks...")
    check_hardlinks_and_clones(out_lines)
    OUT.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"Listo. Revisa {OUT.resolve()}")
