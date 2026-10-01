r"""
Traduce las rutas de la BD procesada en la nube a las rutas locales de tus discos.

En el droplet las fotos viven en /mnt/fotos/Organizar_HDD_F_F:/fotos/x.jpg
En tu PC la misma foto está en F:\fotos\x.jpg

Sin esta traducción, la Fase 4 (export) no encontraría ningún archivo.

    python remapear_rutas.py photos_nube.db
    python remapear_rutas.py photos_nube.db --aplicar     # escribe los cambios
"""
import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# (prefijo en la nube, raíz local). El orden importa: gana el primero que matchea.
MAPEOS = [
    ("/mnt/fotos/Organizar_HDD_E_E:/", "E:/"),
    ("/mnt/fotos/Organizar_HDD_F_F:/", "F:/"),
    ("/mnt/fotos/Organizar_HDD_E_E_/", "E:/"),   # variante sin dos puntos
    ("/mnt/fotos/Organizar_HDD_F_F_/", "F:/"),
    ("/mnt/fotos/HDD_E/E:/", "E:/"),
    ("/mnt/fotos/HDD_F/F:/", "F:/"),
]

# Estas carpetas venían solo de Drive: no tienen equivalente local.
SIN_LOCAL = ("GDrive_MiDrive", "Cosas mama", "Cosas_mama", "ColegioYWeas", "Lightroom")


def traducir(ruta_nube: str) -> str | None:
    r = ruta_nube.replace("\\", "/")
    for prefijo, local in MAPEOS:
        if r.startswith(prefijo):
            return (local + r[len(prefijo):]).replace("/", "\\")
    return None


def main():
    ap = argparse.ArgumentParser(description="Remapear rutas de la nube a locales")
    ap.add_argument("db", help="BD bajada del droplet (ej: photos_nube.db)")
    ap.add_argument("--aplicar", action="store_true",
                    help="Escribir los cambios (por defecto solo simula)")
    ap.add_argument("--verificar", action="store_true",
                    help="Comprobar que cada archivo exista en el disco (lento)")
    args = ap.parse_args()

    if not Path(args.db).exists():
        print(f"No existe: {args.db}")
        return 1

    conn = sqlite3.connect(args.db)
    filas = conn.execute("SELECT id, file_path FROM files").fetchall()
    print(f"Fotos en la BD: {len(filas):,}")

    cambios, sin_mapeo = [], Counter()
    for pid, ruta in filas:
        if ruta and (ruta[1:3] == ":\\" or ruta[1:3] == ":/"):
            continue  # ya es una ruta local de Windows
        nueva = traducir(ruta or "")
        if nueva:
            cambios.append((nueva, pid))
        else:
            marca = next((s for s in SIN_LOCAL if s in (ruta or "")), "otro")
            sin_mapeo[marca] += 1

    print(f"  se pueden traducir : {len(cambios):,}")
    if sin_mapeo:
        print("  sin equivalente local (quedan como están, solo viven en Drive):")
        for k, v in sin_mapeo.most_common():
            print(f"     {k:<28} {v:>7,}")

    if cambios:
        print("\nEjemplos:")
        for nueva, pid in cambios[:3]:
            vieja = next(r for i, r in filas if i == pid)
            print(f"   {vieja}\n     -> {nueva}")

    if args.verificar and cambios:
        print("\nVerificando existencia en disco...")
        faltan = sum(1 for nueva, _ in cambios if not Path(nueva).exists())
        print(f"   encontradas: {len(cambios)-faltan:,}   faltan: {faltan:,}")
        if faltan > len(cambios) * 0.1:
            print("   AVISO: faltan mas del 10%. Revisá que los discos estén conectados")
            print("   con las mismas letras (E: y F:) que cuando se subieron.")

    if not args.aplicar:
        print("\nSimulación. Para escribir los cambios:")
        print(f"   python remapear_rutas.py {args.db} --aplicar")
        conn.close()
        return 0

    conn.executemany("UPDATE files SET file_path=? WHERE id=?", cambios)
    conn.commit()
    print(f"\n✅ {len(cambios):,} rutas actualizadas en {args.db}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
