r"""
Cruza dos senales para saber que archivos estan danados y, sobre todo, CUANDO.

  - marcador de fin de JPEG (ffd9): evidencia real de dano, no depende de la base
  - SHA256 guardado al indexar vs el del archivo hoy

Combinados dan cuatro casos, y la diferencia importa:

  sano + hash OK            el archivo esta bien
  truncado + hash OK        ya estaba roto CUANDO se indexo -> viene del rescate
                            de F:, no lo rompio nada despues. No hay nada que
                            recuperar: asi se rescato.
  truncado + hash distinto  se rompio DESPUES de indexarlo -> copia o disco
  sano + hash distinto      el archivo esta bien y el hash de la base quedo
                            viejo (tipico despues de remapear rutas)

OJO: no alcanza con comparar contra files.bytes para hablar de "corrupcion
silenciosa". Esa columna la llena rellenar_metadatos() midiendo el archivo que
esta hoy en el disco, asi que comparar el tamano actual contra ella no prueba
nada. El marcador ffd9 si es independiente.

    python verificar_integridad.py                # muestra de 600
    python verificar_integridad.py -n 5000        # muestra mas grande
    python verificar_integridad.py --todos        # el disco entero (horas)
    python verificar_integridad.py --csv roto.csv # deja la lista de danados

Solo lee. No toca ni la base ni los archivos.
"""
import argparse
import collections
import csv
import hashlib
import os
import random
import sqlite3
import sys
import time

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
FIN_JPEG = b"\xff\xd9"
JPEG = (".jpg", ".jpeg")

SANO_OK = "sano + hash OK"
ROTO_ANTES = "truncado + hash OK (ya venia roto del rescate)"
ROTO_DESPUES = "truncado + hash distinto (se rompio despues de indexar)"
HASH_VIEJO = "sano + hash distinto (el archivo esta bien, el hash quedo viejo)"
ORDEN = [SANO_OK, ROTO_ANTES, ROTO_DESPUES, HASH_VIEJO]


def leer(path, bloque=1 << 20):
    """SHA256 y los ultimos 2 bytes, en UNA sola pasada.

    Sacar el marcador de fin con un seek aparte obliga a abrir el archivo dos
    veces: el cabezal va al final y despues vuelve al principio a leer. Con
    109k archivos en un disco USB eso solo son horas de seeks. Como igual hay
    que leerlo entero para el hash, el ultimo bloque ya trae el final.
    """
    h = hashlib.sha256()
    cola = b""
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(bloque), b""):
            h.update(b)
            cola = (cola + b)[-2:] if len(b) < 2 else b[-2:]
    return h.hexdigest(), cola


def categoria(ruta, sha_db, sha, cola):
    """Clasifica a partir de valores ya leidos. Pura: no toca el disco, asi
    quien ya leyo el archivo (ej. refrescar_hashes.py) no lo lee dos veces."""
    hash_ok = sha == sha_db
    # Solo el JPEG tiene un marcador de fin que sirva de senal independiente.
    trunc = (cola != FIN_JPEG) if os.path.splitext(ruta)[1].lower() in JPEG else None
    if trunc is None:                       # RAW: sin senal independiente
        return (SANO_OK if hash_ok else HASH_VIEJO), hash_ok, None
    if hash_ok:
        return (ROTO_ANTES if trunc else SANO_OK), hash_ok, trunc
    return (ROTO_DESPUES if trunc else HASH_VIEJO), hash_ok, trunc


def clasificar(ruta, sha_db):
    """Devuelve (categoria, hash_ok, truncado). Puede levantar OSError."""
    sha, cola = leer(ruta)
    return categoria(ruta, sha_db, sha, cola)


def raiz(p):
    partes = p.split("\\")
    return partes[1] if len(partes) > 1 else p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=600, help="tamano de la muestra")
    ap.add_argument("--todos", action="store_true", help="verificar todo")
    ap.add_argument("--csv", help="guardar los danados en este CSV")
    ap.add_argument("--solo-jpeg", action="store_true",
                    help="solo JPEG (los unicos con senal independiente)")
    args = ap.parse_args()

    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    # bytes sale de la base: medir los 109k archivos con stat antes de empezar
    # era una pasada entera de mas sobre el disco, solo para calcular el ETA.
    filas = [r for r in c.execute(
        """SELECT id, file_path, file_hash_sha256, COALESCE(bytes,0) FROM files
           WHERE file_hash_sha256 IS NOT NULL AND file_hash_sha256 <> ''
             AND COALESCE(borrado,0)=0""")
        if not args.solo_jpeg or os.path.splitext(r[1])[1].lower() in JPEG]
    print(f"archivos con hash guardado: {len(filas):,}")
    if not args.todos:
        random.seed(20260801)              # misma muestra entre corridas
        filas = random.sample(filas, min(args.n, len(filas)))
    print(f"verificando {len(filas):,}...\n")

    cont = collections.Counter()
    por_carpeta = collections.defaultdict(collections.Counter)
    danados, t0, leido = [], time.time(), 0
    total_bytes = sum(b for _, _, _, b in filas) or 1

    # El CSV se escribe a medida y no al final: un barrido completo son horas y
    # si se corta a mitad de camino no se pierde lo ya verificado.
    fcsv = wcsv = None
    if args.csv:
        fcsv = open(args.csv, "w", encoding="utf-8", newline="")
        wcsv = csv.writer(fcsv)
        wcsv.writerow(["id", "ruta", "estado"])

    try:
        for i, (pid, ruta, sha_db, nbytes) in enumerate(filas, 1):
            leido += nbytes
            try:
                cat, hash_ok, trunc = clasificar(ruta, sha_db)
            except FileNotFoundError:
                # se deja que falle el open en vez de preguntar antes con
                # exists(): es un stat menos por archivo.
                cont["no esta en el disco"] += 1
                continue
            except OSError as e:
                cont["ilegible"] += 1
                cat = f"no se pudo leer: {e}"
                danados.append((pid, ruta, cat))
                if wcsv:
                    wcsv.writerow([pid, ruta, cat]); fcsv.flush()
                continue
            cont[cat] += 1
            por_carpeta[raiz(ruta)][cat] += 1
            if cat in (ROTO_ANTES, ROTO_DESPUES):
                danados.append((pid, ruta, cat))
                if wcsv:
                    wcsv.writerow([pid, ruta, cat]); fcsv.flush()
            if i % 200 == 0:
                seg = time.time() - t0
                falta = seg / max(leido, 1) * (total_bytes - leido)
                print(f"  {i:,}/{len(filas):,}  {leido/1024**3:.1f}/{total_bytes/1024**3:.1f} GB"
                      f"  {leido/1024**2/max(seg,1):.0f} MB/s  faltan ~{falta/60:.0f} min",
                      file=sys.stderr)
    finally:
        if fcsv:
            fcsv.close()

    tot = sum(cont[k] for k in ORDEN)
    print(f"revisados con exito: {tot:,}\n")
    for k in ORDEN:
        n = cont[k]
        print(f"  {n:6,}  ({n/tot*100:5.1f}%)  {k}" if tot else f"  {n:6,}  {k}")
    for k in ("no esta en el disco", "ilegible"):
        if cont[k]:
            print(f"  {cont[k]:6,}           {k}")

    rotos = cont[ROTO_ANTES] + cont[ROTO_DESPUES]
    if tot:
        print(f"\ndanados en total: {rotos:,} ({rotos/tot*100:.1f}%)")
        print(f"de esos, ya venian rotos del rescate: {cont[ROTO_ANTES]:,}"
              f" ({cont[ROTO_ANTES]/rotos*100:.0f}%)" if rotos else "")

    print("\npor carpeta (solo donde hay dano):")
    for carpeta, c2 in sorted(por_carpeta.items(), key=lambda kv: -sum(kv[1].values())):
        n = sum(c2.values())
        r = c2[ROTO_ANTES] + c2[ROTO_DESPUES]
        if r:
            print(f"  {carpeta[:44]:44} {r:5,}/{n:5,} danados ({r/n*100:4.1f}%)")

    if args.csv:
        print(f"\nlista completa en {args.csv} ({len(danados):,} archivos)")


if __name__ == "__main__":
    main()
