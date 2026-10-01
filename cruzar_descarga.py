r"""
Cruza una carpeta recien descargada (OneDrive, export de Lightroom) contra la
base, para responder tres cosas:

  1. que archivos DAÑADOS se pueden reemplazar con lo descargado
  2. que sigue faltando (ni en K: ni en la descarga)
  3. que material es NUEVO, o sea que no estaba en la base

Como empareja, de mas a menos confiable:

  sha256      el archivo descargado es identico al que la base registro cuando
              indexo. Es la prueba mas fuerte: para los que se rompieron
              DESPUES de indexar, el hash guardado es todavia el de la version
              sana, asi que un match aca es el original intacto.
  nombre+fecha mismo nombre de archivo y misma fecha de captura EXIF.
  nombre      solo el nombre. Sugerencia: hay que mirarlo.

NO se empareja por phash contra los dañados a proposito. El phash de un archivo
roto se calculo decodificando datos corruptos, asi que no representa la foto:
emparejar contra eso daria coincidencias inventadas. El phash si se usa, y solo
cuando hace falta, para no marcar como "nueva" una foto que ya existe reescrita.

    python cruzar_descarga.py "D:\OneDrive"           # indexa y cruza
    python cruzar_descarga.py "D:\OneDrive" --limite 500

El indice de la descarga se guarda y se reusa: volver a correrlo no rehashea
lo ya visto.
"""
import argparse
import collections
import csv
import ntpath
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils_images import compute_sha256, is_image_file, read_exif_info  # noqa: E402

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
DANADOS = ["integridad_completa.csv", "raw_rotos.csv"]

SHA = "sha256 (original intacto)"
NOMBRE_FECHA = "nombre + fecha"
NOMBRE = "solo nombre (revisar)"
MISMO_ROTO = "el descargado es el MISMO archivo roto"


def sha_sirve(estado):
    """El sha256 guardado solo prueba 'estaba sano' si el daño vino DESPUES.

    Si el archivo ya estaba roto cuando se indexo, la base guardo el hash del
    archivo ROTO: que la descarga coincida significa que bajaste la misma
    version rota, no que la recuperaste. Los RAW estan en el mismo caso porque
    sus hashes se refrescaron contra el archivo actual.
    """
    return "hash distinto" in estado


def cargar_danados():
    """ruta -> estado, juntando el barrido de JPEG y el de RAW."""
    d = {}
    for nombre in DANADOS:
        if not os.path.exists(nombre):
            print(f"  (falta {nombre}, sigo sin el)")
            continue
        with open(nombre, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("estado", "").startswith("NO decodifica") or "truncado" in r.get("estado", ""):
                    d[r["ruta"]] = r["estado"]
    return d


def indexar(carpeta, indice, limite=None):
    """Recorre la descarga calculando sha256 y fecha. Reanudable."""
    ya = {}
    if os.path.exists(indice):
        with open(indice, encoding="utf-8") as fh:
            ya = {r["ruta"]: r for r in csv.DictReader(fh)}
        print(f"  indice previo: {len(ya):,} archivos ya vistos")

    nuevos, t0 = [], time.time()
    for raiz, _, archivos in os.walk(carpeta):
        for a in archivos:
            p = os.path.join(raiz, a)
            if p in ya or not is_image_file(p):
                continue
            nuevos.append(p)
            if limite and len(nuevos) >= limite:
                break
        if limite and len(nuevos) >= limite:
            break
    print(f"  archivos nuevos a indexar: {len(nuevos):,}")

    modo = "a" if ya else "w"
    with open(indice, modo, encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        if modo == "w":
            w.writerow(["ruta", "nombre", "bytes", "sha256", "fecha"])
        for i, p in enumerate(nuevos, 1):
            try:
                fecha, _ = read_exif_info(p)
                fila = [p, ntpath.basename(p).lower(), os.path.getsize(p),
                        compute_sha256(p), (fecha or "")[:10]]
            except OSError:
                continue
            w.writerow(fila)
            ya[p] = dict(zip(["ruta", "nombre", "bytes", "sha256", "fecha"], fila))
            if i % 200 == 0:
                fh.flush()
                print(f"    {i:,}/{len(nuevos):,} ({time.time()-t0:.0f}s)", file=sys.stderr)
    return ya


def cruzar(danados, descarga, filas_db):
    """Devuelve (recuperables, huerfanos, nuevas)."""
    por_sha = collections.defaultdict(list)
    por_nom_fecha = collections.defaultdict(list)
    por_nom = collections.defaultdict(list)
    for d in descarga.values():
        por_sha[d["sha256"]].append(d)
        por_nom_fecha[(d["nombre"], d["fecha"])].append(d)
        por_nom[d["nombre"]].append(d)

    recuperables, huerfanos = [], []
    for ruta, estado in danados.items():
        reg = filas_db.get(ruta)
        if not reg:
            continue
        nom = ntpath.basename(ruta).lower()
        fecha = (reg["capture_date"] or "")[:10]
        cand = metodo = None
        mismo = reg["sha"] and por_sha.get(reg["sha"])
        if mismo and sha_sirve(estado):
            cand, metodo = mismo[0], SHA
        elif mismo:
            # coincide con el hash guardado, pero ese hash ES el del archivo
            # roto: bajaste la misma version rota. No sirve de reemplazo.
            cand, metodo = mismo[0], MISMO_ROTO
        elif por_nom_fecha.get((nom, fecha)):
            cand, metodo = por_nom_fecha[(nom, fecha)][0], NOMBRE_FECHA
        elif por_nom.get(nom):
            cand, metodo = por_nom[nom][0], NOMBRE
        if cand and metodo != MISMO_ROTO:
            recuperables.append([ruta, reg["evento"] or "", fecha, estado,
                                 metodo, cand["ruta"], cand["bytes"]])
        else:
            # sin candidato, o el unico candidato es la misma version rota
            huerfanos.append([ruta, reg["evento"] or "", fecha, estado,
                              cand["ruta"] if cand else "", metodo or ""])

    # material nuevo: no coincide con NINGUN archivo de la base
    shas_db = {r["sha"] for r in filas_db.values() if r["sha"]}
    nom_fecha_db = {(ntpath.basename(p).lower(), (r["capture_date"] or "")[:10])
                    for p, r in filas_db.items()}
    nuevas = [[d["ruta"], d["fecha"], d["bytes"]] for d in descarga.values()
              if d["sha256"] not in shas_db
              and (d["nombre"], d["fecha"]) not in nom_fecha_db]
    return recuperables, huerfanos, nuevas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("carpeta", help="carpeta descargada (OneDrive, Lightroom...)")
    ap.add_argument("--indice", default="descarga_indice.csv")
    ap.add_argument("--limite", type=int, help="para probar con pocos archivos")
    ap.add_argument("--prefijo", default="",
                    help="antepone esto a los CSV de salida, para cruzar varias "
                         "carpetas sin que una pise los resultados de la otra")
    args = ap.parse_args()
    if not os.path.isdir(args.carpeta):
        sys.exit(f"no existe la carpeta {args.carpeta}")

    print("1. dañados conocidos")
    danados = cargar_danados()
    print(f"   {len(danados):,}\n")

    print(f"2. indexando {args.carpeta}")
    descarga = indexar(args.carpeta, args.indice, args.limite)
    print(f"   {len(descarga):,} imagenes en la descarga\n")

    print("3. leyendo la base")
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    filas_db = {r["file_path"]: {"sha": r["file_hash_sha256"],
                                 "capture_date": r["capture_date"],
                                 "evento": r["evento"]}
                for r in c.execute("""
        SELECT f.file_path, f.file_hash_sha256, f.capture_date,
               (SELECT COALESCE(NULLIF(e.name,''), e.suggested_name)
                FROM events e JOIN event_photos ep ON ep.event_id=e.id
                WHERE ep.photo_id=f.id LIMIT 1) evento
        FROM files f WHERE COALESCE(f.borrado,0)=0""")}
    print(f"   {len(filas_db):,} archivos en la base\n")

    rec, huer, nuevas = cruzar(danados, descarga, filas_db)

    mismos = sum(1 for h in huer if h[5] == MISMO_ROTO)
    print("=" * 60)
    print(f"  RECUPERABLES con esta descarga : {len(rec):,}")
    for m, n in collections.Counter(x[4] for x in rec).most_common():
        print(f"      por {m:28} {n:6,}")
    print(f"  siguen sin aparecer            : {len(huer):,}")
    if mismos:
        print(f"      de esos, bajaste la misma version rota: {mismos:,}")
    print(f"  material NUEVO (no estaba)     : {len(nuevas):,}")
    print("=" * 60)

    for nombre, filas, cab in (
            (args.prefijo + "recuperables.csv", rec,
             ["ruta_dañada", "evento", "fecha", "estado", "metodo", "reemplazo", "bytes"]),
            (args.prefijo + "siguen_faltando.csv", huer,
             ["ruta_dañada", "evento", "fecha", "estado", "candidato_inservible", "motivo"]),
            (args.prefijo + "material_nuevo.csv", nuevas, ["ruta", "fecha", "bytes"])):
        with open(nombre, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cab)
            w.writerows(sorted(filas))
        print(f"  {nombre}")


if __name__ == "__main__":
    main()
