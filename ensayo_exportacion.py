r"""
Muestra como quedaria el archivo exportado por la Fase 4, sin mover un archivo.

No duplica la logica del exportador: importa `4_exportar` y le cambia las tres
funciones que tocan el disco —place_file, Path.exists y Path.mkdir— por
versiones que solo anotan. Asi los nombres de carpeta son exactamente los que
saldrian de verdad, y si manana cambia el exportador, este ensayo cambia solo.

Saltear `exists()` ademas evita 178.000 consultas a K: por red, que serian
horas: acá el ensayo tarda segundos.

    python ensayo_exportacion.py
    python ensayo_exportacion.py --min-fotos-evento 20
"""
import argparse
import importlib
import sys
from collections import Counter
from pathlib import Path, WindowsPath, PosixPath

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SALIDA = "ensayo_exportacion.txt"
BASE = WindowsPath if sys.platform == "win32" else PosixPath


class RutaSeca(BASE):
    """Una ruta que nunca toca el disco: todo existe, nada se crea."""
    def exists(self, *a, **k):
        return True

    def mkdir(self, *a, **k):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-fotos-evento", type=int, default=4)
    ap.add_argument("--out", default=r"K:\Fotos_Organizadas_IA")
    a = ap.parse_args()

    m = importlib.import_module("4_exportar")
    destinos = []
    m.place_file = lambda src, dest, mode: (destinos.append(dest), True)[1]
    m.Path = RutaSeca

    m.run(out_dir=a.out, mode="link", min_fotos_evento=a.min_fotos_evento)

    raiz = RutaSeca(a.out)
    carpetas = Counter(str(d.parent) for d in destinos)
    print(f"\n{'='*66}\nENSAYO: {len(destinos):,} fotos en {len(carpetas):,} carpetas\n{'='*66}")

    # Primer nivel, que es como se ve el archivo al abrirlo
    primero = Counter()
    for d in destinos:
        rel = d.parent.relative_to(raiz).parts
        primero[rel[0] if rel else "(raiz)"] += 1
    print("\nprimer nivel:")
    for k, v in primero.most_common():
        print(f"   {v:8,}  {k}")

    with open(SALIDA, "w", encoding="utf-8") as fh:
        fh.write(f"Ensayo de la Fase 4 - {len(destinos):,} fotos, "
                 f"{len(carpetas):,} carpetas (min-fotos-evento={a.min_fotos_evento})\n\n")
        for c, n in sorted(carpetas.items()):
            fh.write(f"{n:7,}  {c}\n")
    print(f"\narbol completo en {SALIDA}")

    print("\nlas 15 carpetas mas grandes:")
    for c, n in carpetas.most_common(15):
        print(f"   {n:7,}  {c}")


if __name__ == "__main__":
    main()
