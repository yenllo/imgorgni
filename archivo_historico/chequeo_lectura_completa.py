"""Lee cada archivo del disco por completo para detectar bloques ilegibles,
sin usar chkdsk (que no es resumible: interrumpirlo lo hace arrancar de cero
la proxima vez). Esto SI es resumible: guarda que archivos ya verifico en un
checkpoint, asi que se puede parar (Ctrl+C, cerrar la terminal) y correr de
nuevo mas tarde, retoma donde quedo sin re-leer nada.

Nota: como usa lectura normal de archivos (no acceso a disco crudo), Windows
puede cachear en RAM lo ya leido -- para la primera pasada sobre un disco
que no se toco antes esto no importa (todo se lee de verdad del disco).

    python chequeo_lectura_completa.py E:
    python chequeo_lectura_completa.py G:
"""
import sys
import time
from pathlib import Path

CHUNK = 4 * 1024 * 1024  # 4MB


def main():
    if len(sys.argv) < 2:
        print("uso: python chequeo_lectura_completa.py <letra_unidad>: (ej: E:)")
        return
    letra = sys.argv[1].rstrip(":")
    raiz = Path(letra + ":\\")
    checkpoint = Path(f"chequeo_{letra}_hecho.txt")
    errores_log = Path(f"chequeo_{letra}_errores.txt")

    ya_hechos = set()
    if checkpoint.exists():
        ya_hechos = set(checkpoint.read_text(encoding="utf-8", errors="ignore").splitlines())

    print("listando archivos...", flush=True)
    todos = [p for p in raiz.rglob("*") if p.is_file()]
    pendientes = [p for p in todos if str(p) not in ya_hechos]
    print(f"total: {len(todos):,}  ya verificados: {len(ya_hechos):,}  "
          f"pendientes: {len(pendientes):,}", flush=True)

    t0 = time.time()
    con_error = 0
    with open(checkpoint, "a", encoding="utf-8") as chk, \
         open(errores_log, "a", encoding="utf-8") as errlog:
        for i, p in enumerate(pendientes, 1):
            try:
                with open(p, "rb") as f:
                    while f.read(CHUNK):
                        pass
            except OSError as e:
                con_error += 1
                linea = f"{p}\t{e}"
                print("  ERROR:", linea, flush=True)
                errlog.write(linea + "\n")
                errlog.flush()
            chk.write(str(p) + "\n")
            if i % 500 == 0:
                chk.flush()
                elapsed = time.time() - t0
                ritmo = i / elapsed
                eta = (len(pendientes) - i) / ritmo / 60 if ritmo else 0
                print(f"  {i:,}/{len(pendientes):,}  ({con_error} con error)  "
                      f"ETA {eta:.0f} min", flush=True)

    print(f"listo. {con_error} archivos con error (ver {errores_log})")


if __name__ == "__main__":
    main()
