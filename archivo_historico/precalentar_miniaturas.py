r"""
Genera por adelantado las miniaturas de los duplicados pendientes.

/thumb las arma la primera vez que se pide y las cachea en _thumbs_curador.
Con 39 mil por hacer -- la mitad RAW, que hay que abrir con rawpy desde el
disco externo -- revisar duplicados es esperar al disco en cada pagina. Esto
las hace todas de una, para que despues la web vuele.

Pide al servidor que ya esta corriendo en vez de repetir su logica: asi usa
exactamente el mismo codigo y el mismo cache, sin una segunda version que se
desincronice.

    python precalentar_miniaturas.py                # los pares pendientes
    python precalentar_miniaturas.py --todo         # todas las fotos vivas
    python precalentar_miniaturas.py --hilos 6

Es reanudable: saltea lo que ya existe. Cortarlo no rompe nada.
"""
import argparse
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
THUMBS = r"C:\Renzo\Proyectos\IMGorgni\_thumbs_curador"
BASE = "http://127.0.0.1:5000"


def pendientes(c):
    ids = set()
    for a, b in c.execute("""
            SELECT id_conservar, id_descartar FROM duplicados
            WHERE decision='pendiente'
              AND NOT EXISTS (SELECT 1 FROM files f
                              WHERE f.id IN (duplicados.id_conservar, duplicados.id_descartar)
                                AND f.borrado=1)"""):
        ids.update((a, b))
    ids.discard(None)
    return ids


def todas(c):
    return {r[0] for r in c.execute(
        "SELECT id FROM files WHERE COALESCE(borrado,0)=0")}


def faltantes(ids):
    """Las que todavia no estan en el cache. Hace que sea reanudable."""
    hay = {int(n[:-4]) for n in os.listdir(THUMBS)
           if n.endswith(".jpg") and n[:-4].isdigit()} if os.path.isdir(THUMBS) else set()
    return sorted(ids - hay)


def pedir(pid):
    """True si quedo una miniatura de verdad. Un cartel SVG (archivo dañado o
    no encontrado) devuelve 200 pero no genera cache: no cuenta como hecha."""
    try:
        with urllib.request.urlopen(f"{BASE}/thumb/{pid}", timeout=180) as r:
            return r.headers.get("Content-Type", "").startswith("image/jpeg")
    except (urllib.error.URLError, OSError):
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--todo", action="store_true", help="todas las fotos, no solo duplicados")
    ap.add_argument("--hilos", type=int, default=4,
                    help="4 va bien en disco externo; mas lo hace pelear consigo mismo")
    args = ap.parse_args()

    try:
        urllib.request.urlopen(BASE + "/", timeout=20).close()
    except Exception:
        sys.exit(f"el curador no responde en {BASE}: levantalo con "
                 f"'python web_curador.py' antes de correr esto")

    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    ids = todas(c) if args.todo else pendientes(c)
    c.close()
    faltan = faltantes(ids)
    print(f"fotos a cubrir : {len(ids):,}")
    print(f"ya en cache    : {len(ids) - len(faltan):,}")
    print(f"por generar    : {len(faltan):,}\n")
    if not faltan:
        return

    hechas = fallidas = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.hilos) as ex:
        for i, ok in enumerate(ex.map(pedir, faltan), 1):
            hechas += ok
            fallidas += not ok
            if i % 250 == 0:
                seg = time.time() - t0
                falta = seg / i * (len(faltan) - i)
                print(f"  {i:,}/{len(faltan):,}  {hechas:,} hechas  {fallidas:,} sin miniatura"
                      f"  {i/seg:.1f}/s  faltan ~{falta/60:.0f} min", file=sys.stderr)

    print(f"terminado en {(time.time()-t0)/60:.0f} min")
    print(f"  {hechas:,} miniaturas nuevas")
    print(f"  {fallidas:,} sin miniatura (archivo dañado, ilegible o ausente)")


if __name__ == "__main__":
    main()
