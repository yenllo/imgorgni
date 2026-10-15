r"""
Que archivos dañados se pueden recuperar SIN descargar nada, porque ya hay una
copia sana del mismo archivo en otro lugar de K:.

Junta las dos fuentes de daño (JPEG por marcador de fin, RAW por decodificado)
y para cada dañado busca un archivo con el mismo nombre que NO figure como
dañado en ninguna de las dos. Como los dos barridos cubrieron el disco entero,
"no figura" equivale a "verificado sano".

El emparejamiento por nombre se confirma con fecha de captura y tamaño para no
juntar dos fotos distintas que se llaman igual (DSC01234.JPG se repite entre
tarjetas). El resultado dice con que confianza quedo cada uno.

    python plan_rescate_local.py

Solo lee. Genera rescate_local.csv y falta_descargar.csv.
"""
import collections
import csv
import ntpath
import os
import sqlite3

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
FUENTES = ["integridad_completa.csv", "raw_rotos.csv"]

FUERTE = "nombre + fecha + tamaño"
MEDIA = "nombre + fecha"
DEBIL = "solo nombre (revisar)"


def cargar_danados():
    d = {}
    for f in FUENTES:
        if not os.path.exists(f):
            print(f"  falta {f}, sigo sin el")
            continue
        with open(f, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                e = r.get("estado", "")
                if "truncado" in e or e.startswith("NO decodifica"):
                    d[r["ruta"]] = e
    return d


def elegir(cands, fecha, tam):
    """El mejor candidato y con que confianza."""
    for c in cands:
        if c["fecha"] == fecha and c["bytes"] == tam and fecha:
            return c, FUERTE
    for c in cands:
        if c["fecha"] == fecha and fecha:
            return c, MEDIA
    return (cands[0], DEBIL) if cands else (None, None)


def main():
    print("1. dañados")
    danados = cargar_danados()
    print(f"   {len(danados):,}\n")

    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    filas = c.execute("""
        SELECT f.file_path, f.capture_date, f.bytes,
               (SELECT COALESCE(NULLIF(e.name,''), e.suggested_name)
                FROM events e JOIN event_photos ep ON ep.event_id=e.id
                WHERE ep.photo_id=f.id LIMIT 1) evento
        FROM files f WHERE COALESCE(f.borrado,0)=0""").fetchall()
    print(f"2. base: {len(filas):,} archivos")

    sanos = collections.defaultdict(list)
    for r in filas:
        if r["file_path"] not in danados:
            sanos[ntpath.basename(r["file_path"]).lower()].append(
                {"ruta": r["file_path"], "fecha": (r["capture_date"] or "")[:10],
                 "bytes": r["bytes"]})
    info = {r["file_path"]: r for r in filas}

    rescate, faltan = [], []
    for ruta, estado in danados.items():
        reg = info.get(ruta)
        if not reg:
            continue
        cands = sanos.get(ntpath.basename(ruta).lower(), [])
        cand, conf = elegir(cands, (reg["capture_date"] or "")[:10], reg["bytes"])
        fila = [ruta, reg["evento"] or "", (reg["capture_date"] or "")[:10], estado]
        if cand:
            rescate.append(fila + [conf, cand["ruta"]])
        else:
            faltan.append(fila)

    print()
    print("=" * 62)
    print(f"  se recuperan desde K:, sin descargar : {len(rescate):,}")
    for k, n in collections.Counter(x[4] for x in rescate).most_common():
        print(f"      {k:26} {n:6,}")
    print(f"  hay que buscarlos afuera             : {len(faltan):,}")
    print("=" * 62)
    print("\n  por evento, lo que falta descargar:")
    for k, n in collections.Counter(x[1] or "(sin evento)" for x in faltan).most_common(10):
        print(f"    {n:6,}  {k[:56]}")

    for nombre, datos, cab in (
            ("rescate_local.csv", rescate,
             ["ruta_dañada", "evento", "fecha", "estado", "confianza", "copia_sana"]),
            ("falta_descargar.csv", faltan,
             ["ruta_dañada", "evento", "fecha", "estado"])):
        with open(nombre, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cab)
            w.writerows(sorted(datos))
        print(f"\n  {nombre}  ({len(datos):,})")


if __name__ == "__main__":
    main()
