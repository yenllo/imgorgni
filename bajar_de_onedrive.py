r"""
Baja de OneDrive solo las imágenes, directo a K:, sin llenar C:.

Los archivos de OneDrive son marcadores: leerlos los descarga a C: y ahí se
quedan ocupando espacio. Este script, por cada archivo:

    1. lo copia a K:  (la lectura lo descarga)
    2. lo vuelve "solo en la nube" con `attrib +U -P`, liberando C:

Así C: nunca crece: el pico es un archivo, no los 690 GB.

NUNCA borra ni mueve nada dentro de la carpeta de OneDrive. Mover o borrar ahí
se sincroniza y te lo borra de la nube. Solo copia y deshidrata, que es
manejo de espacio local y no toca el original.

    python bajar_de_onedrive.py --simular                  # ver qué haría
    python bajar_de_onedrive.py --ramas otroPC Desktop     # bajar esas ramas
    python bajar_de_onedrive.py --solo-utiles              # saltea lo que ya tenés

Es reanudable: si el destino ya tiene el archivo con el mismo tamaño, lo saltea.
Cortarlo con Ctrl+C no rompe nada.
"""
import argparse
import csv
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

ORIGEN = r"C:\Users\renzo\OneDrive"
DESTINO = r"K:\OneDrive_bajado"
CSV_MAPA = r"C:\Renzo\Proyectos\IMGorgni\onedrive_carpetas.csv"
IMG = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff",
       ".cr2", ".arw", ".nef", ".rw2", ".raf", ".raw", ".dng", ".orf", ".srw"}
MIN_LIBRE_GB = 20        # si C: baja de esto, frenamos
REINTENTOS = 3           # por archivo, ante WinError 389 y demas
ESPERA = 5               # segundos del primer reintento (despues 10, 20)
SEGUIDAS_MAX = 25        # fallos consecutivos que se leen como "me cortaron"
PAUSA = 600              # 10 min de pausa cuando eso pasa


def libre_gb(unidad):
    return shutil.disk_usage(unidad).free / 1024**3


def deshidratar(path):
    """Vuelve el archivo 'solo en la nube'. Libera el espacio en C: sin tocar
    la copia de la nube: +U = unpinned, -P = no anclado localmente."""
    try:
        subprocess.run(["attrib", "+U", "-P", path], check=False,
                       capture_output=True, timeout=30)
    except Exception:
        pass


def carpetas_utiles():
    """Las que el mapeo marcó como que aportan algo."""
    try:
        with open(CSV_MAPA, encoding="utf-8") as fh:
            return {r["carpeta"] for r in csv.DictReader(fh)
                    if not r["veredicto"].startswith("no hace falta")}
    except FileNotFoundError:
        sys.exit(f"falta {CSV_MAPA}: corré antes mapear_onedrive.py")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--origen", default=ORIGEN)
    ap.add_argument("--destino", default=DESTINO)
    ap.add_argument("--ramas", nargs="*", help="ramas de primer nivel (ej: otroPC Desktop)")
    ap.add_argument("--solo-utiles", action="store_true",
                    help="saltear carpetas que el mapeo marcó como 'no hace falta'")
    ap.add_argument("--simular", action="store_true")
    ap.add_argument("--limite", type=int, help="cortar despues de N archivos")
    ap.add_argument("--log", help="CSV con una fila por archivo (default: segun la rama)")
    ap.add_argument("--min-kb", type=int, default=100,
                    help="ignorar imagenes mas chicas que esto. No es por espacio "
                         "(las 10.483 menores a 200 KB suman 0,1 GB) sino por "
                         "tiempo: cada archivo cuesta ~1s de hidratacion, asi que "
                         "son ~3 horas bajando iconos de RetroArch y previews de "
                         "juegos de 3DS. Ninguna foto de camara pesa tan poco.")
    ap.add_argument("--hilos", type=int, default=4,
                    help="copias en paralelo. Hidratar cuesta ~1s fijo por archivo "
                         "sin importar el tamaño, asi que el cuello es la latencia "
                         "y no el ancho de banda: varios hilos llenan esos huecos.")
    args = ap.parse_args()
    if not args.log:
        etiqueta = "_".join(args.ramas) if args.ramas else "todo"
        args.log = f"bajada_{etiqueta}.csv"

    utiles = carpetas_utiles() if args.solo_utiles else None
    raices = [os.path.join(args.origen, r) for r in args.ramas] if args.ramas \
        else [args.origen]

    tareas, saltados, bytes_tot, chicos = [], 0, 0, 0
    for raiz in raices:
        if not os.path.isdir(raiz):
            print(f"  ojo: no existe {raiz}")
            continue
        for dp, _, arch in os.walk(raiz):
            if utiles is not None and dp not in utiles:
                continue
            for a in arch:
                if os.path.splitext(a)[1].lower() not in IMG:
                    continue
                src = os.path.join(dp, a)
                dst = os.path.join(args.destino, os.path.relpath(src, args.origen))
                try:
                    n = os.path.getsize(src)
                except OSError:
                    continue
                if n < args.min_kb * 1024:
                    chicos += 1
                    continue
                if os.path.exists(dst) and os.path.getsize(dst) == n:
                    saltados += 1
                    continue
                tareas.append((src, dst, n))
                bytes_tot += n
    if args.limite:
        tareas = tareas[:args.limite]
        bytes_tot = sum(t[2] for t in tareas)

    print(f"a copiar : {len(tareas):,} imágenes  ({bytes_tot/1024**3:,.1f} GB)")
    print(f"ya están : {saltados:,}")
    print(f"salteadas por chicas (<{args.min_kb} KB): {chicos:,}")
    print(f"destino  : {args.destino}")
    print(f"C: libre : {libre_gb('C:'):,.0f} GB     K: libre: {libre_gb('K:'):,.0f} GB")
    if args.simular:
        print("\nSIMULACRO. Primeros 10:")
        for src, dst, n in tareas[:10]:
            print(f"  {n/1024**2:8.1f} MB  {src.replace(args.origen,'…')}")
        return
    if not tareas:
        return

    hechos = fallidos = 0
    t0, copiados = time.time(), 0
    # El log se abre en modo append y se vacía a cada rato: si esto se corta a
    # las 6 horas, queda registrado exactamente qué llegó a copiarse.
    nuevo_log = not os.path.exists(args.log)
    flog = open(args.log, "a", encoding="utf-8", newline="")
    wlog = csv.writer(flog)
    if nuevo_log:
        wlog.writerow(["hora", "estado", "bytes", "origen", "destino", "detalle"])

    def anotar(estado, n, src, dst, detalle=""):
        wlog.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), estado, n, src, dst, detalle])

    lock = threading.Lock()
    frenado = threading.Event()
    # OneDrive corta la hidratacion cuando se le pide demasiado seguido
    # (WinError 389). Anoche fallaron 35.638 archivos de golpe: el primer lote
    # anduvo 16.000 y despues el servicio dijo basta. Se aguanta de dos formas:
    # reintentos con espera creciente por archivo, y una pausa global cuando
    # fallan muchos seguidos, que es la firma del limite de tasa.
    seguidas = [0]

    def copiar(tarea):
        """Una copia, con reintentos. El GIL no molesta: casi todo el tiempo se
        está esperando al disco y a la red, no calculando."""
        nonlocal hechos, fallidos, copiados
        src, dst, n = tarea
        if frenado.is_set():
            return
        ultimo = ""
        for intento in range(REINTENTOS):
            if intento:
                time.sleep(ESPERA * (2 ** (intento - 1)))    # 5s, 10s, 20s
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)      # leer = descargar
                deshidratar(src)            # y devolver el espacio en C:
                with lock:
                    hechos += 1
                    copiados += n
                    seguidas[0] = 0
                    anotar("ok", n, src, dst)
                return
            except Exception as e:
                ultimo = f"{type(e).__name__}: {e}"[:200]
                try:
                    if os.path.exists(dst) and os.path.getsize(dst) != n:
                        os.remove(dst)      # no dejar medio archivo
                except OSError:
                    pass
        with lock:
            fallidos += 1
            seguidas[0] += 1
            anotar("FALLO", n, src, dst, ultimo)
            if fallidos <= 5:
                print(f"  falló {os.path.basename(src)}: {ultimo[:80]}")
            castigo = seguidas[0] >= SEGUIDAS_MAX
        if castigo:
            # el servicio nos corto: esperar y seguir, en vez de quemar la
            # lista entera fallando archivo por archivo como anoche
            print(f"  {SEGUIDAS_MAX} fallos seguidos: pausa de {PAUSA//60} min "
                  f"y sigo", file=sys.stderr)
            with lock:
                anotar("PAUSA", 0, "", "", f"{SEGUIDAS_MAX} fallos seguidos")
                seguidas[0] = 0
            time.sleep(PAUSA)

    with ThreadPoolExecutor(max_workers=args.hilos) as ex:
        for i, _ in enumerate(ex.map(copiar, tareas), 1):
            if i % 50 == 0 and libre_gb("C:") < MIN_LIBRE_GB:
                frenado.set()
                print(f"\nFRENO: C: bajó de {MIN_LIBRE_GB} GB libres. "
                      f"Esperá a que OneDrive libere y volvé a correrlo.")
                with lock:
                    anotar("FRENO", 0, "", "", f"C: con {libre_gb('C:'):.0f} GB libres")
            if i % 100 == 0:
                with lock:
                    flog.flush()
                    seg = time.time() - t0
                    mbs = copiados / 1024**2 / max(seg, 1)
                    resta = (bytes_tot - copiados) / 1024**2 / max(mbs, 0.1) / 60
                    print(f"  {i:,}/{len(tareas):,}  {copiados/1024**3:.1f} GB  "
                          f"{mbs:.0f} MB/s  faltan ~{resta:.0f} min  "
                          f"C: {libre_gb('C:'):,.0f} GB libre", file=sys.stderr)

    flog.close()
    print(f"\ncopiadas {hechos:,} en {(time.time()-t0)/60:.0f} min"
          f"  ({copiados/1024**3:.1f} GB)")
    if fallidos:
        print(f"fallaron {fallidos:,}  (ver estado FALLO en {args.log})")
    print(f"C: libre ahora: {libre_gb('C:'):,.0f} GB")
    print(f"log por archivo: {args.log}")


if __name__ == "__main__":
    main()
