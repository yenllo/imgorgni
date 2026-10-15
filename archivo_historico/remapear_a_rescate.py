r"""
Apunta las rutas F:\... de la BD a su copia sana en E:\Fotos_F_rescatadas\...

Sirve para dos cosas a la vez:
  - el export (Fase 4) pasa de copiar entre discos a hacer hardlinks en E:
    (instantáneo y 0 bytes, porque los hardlinks de NTFS no cruzan volúmenes)
  - deja de leer de F:, que es el disco que se cuelga

Solo remapea si la copia EXISTE de verdad. Lo que no tenga copia se queda
apuntando a F: y se exportará copiando, como antes.

    python remapear_a_rescate.py             # simula
    python remapear_a_rescate.py --aplicar   # escribe
"""
import argparse
import os
import sqlite3
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Orden importa: E: primero porque el export va a E: y ahí el hardlink es gratis.
# C: es el respaldo sano para lo que no llegó a E: (se exportará copiando, pero
# sin el riesgo de que F: cuelgue la lectura).
RESCATES = [r"E:\Fotos_F_rescatadas", r"C:\Fotos_F_rescatadas"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="photos.db")
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--revertir", action="store_true",
                    help="deshacer: volver de E:\\Fotos_F_rescatadas a F:")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, timeout=60)
    if args.revertir:
        filas, cambios = [], []
        for base in RESCATES:
            f = conn.execute("SELECT id, file_path FROM files WHERE file_path LIKE ?",
                             (base + "%",)).fetchall()
            filas += f
            cambios += [("F:\\" + p[len(base) + 1:], i) for i, p in f]
        print(f"revertir: {len(cambios):,} rutas -> F:")
    else:
        filas = conn.execute(
            "SELECT id, file_path FROM files WHERE file_path LIKE 'F:%'").fetchall()
        print(f"rutas en F: dentro de la BD: {len(filas):,}")
        cambios, por_base = [], {b: 0 for b in RESCATES}
        for pid, p in filas:
            for base in RESCATES:
                destino = os.path.join(base, p[3:])   # quita "F:\"
                if os.path.exists(destino):
                    cambios.append((destino, pid))
                    por_base[base] += 1
                    break
        for base, n in por_base.items():
            como = "hardlink, gratis" if base.startswith("E:") else "copia, pero disco sano"
            print(f"  copia en {base}: {n:,}  ({100*n/max(len(filas),1):.0f}%)  -> {como}")
        print(f"  sin copia, siguen en F: {len(filas)-len(cambios):,}")

    if cambios:
        orig = dict(filas)          # id -> ruta vieja, para mostrar el par correcto
        nueva, pid = cambios[0]
        print(f"\nejemplo:\n   {orig[pid]}\n     -> {nueva}")

    if not args.aplicar:
        print("\nSimulación. Para escribir:  python remapear_a_rescate.py --aplicar")
        conn.close()
        return 0

    conn.executemany("UPDATE files SET file_path=? WHERE id=?", cambios)
    conn.commit()
    print(f"\n✅ {len(cambios):,} rutas actualizadas")
    print("   reversible con:  python remapear_a_rescate.py --revertir --aplicar")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
