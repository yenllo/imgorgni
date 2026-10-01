r"""Diagnostico generico de eventos: busca los que se armaron sobre fechas falsas.

El evento 6718 tenia 9.017 fotos en 88 minutos. No era un evento: eran fotos sin
EXIF a las que el indexado les puso como fecha de captura el mtime del archivo
--o sea, el momento en que se copiaron al disco-- y la Fase 3 las agrupo a todas
juntas porque "ocurrieron" al mismo tiempo.

Esto puntua TODOS los eventos con las mismas senales, sin nombres cableados:

  1. densidad imposible : mas fotos por minuto de las que una camara dispara
  2. fecha contradicha  : la ruta o el nombre del archivo dicen otra fecha
  3. sin EXIF de camara : la fecha casi seguro salio del mtime
  4. mezcla incoherente : muchas carpetas de origen y dispositivos distintos

La fecha real se busca en el nombre (YYYYMMDD_HHMMSS, da hora exacta) y en la
carpeta (\2025\2025-07-04\ o \2025-07-04\, da solo el dia).

    python diagnostico_eventos.py              # informe
    python diagnostico_eventos.py --csv out.csv
"""
import argparse
import csv
import os
import re
import sqlite3
from collections import Counter, defaultdict

DB = r"C:\Renzo\Proyectos\IMGorgni\photos_v2.db"

# fecha+hora en el nombre: IMG_20250704_145040, 20230524_195552, VID_20240101-120000
RE_NOMBRE_DT = re.compile(r"(?:^|\D)(\d{4})(\d{2})(\d{2})[_\-](\d{2})(\d{2})(\d{2})(?:\D|$)")
# fecha en el nombre sin hora: 2025-07-04, 20250704
RE_NOMBRE_D = re.compile(r"(?:^|\D)(\d{4})[-_]?(\d{2})[-_]?(\d{2})(?:\D|$)")
# carpeta tipo \2025-07-04\
RE_CARPETA = re.compile(r"[\\/](\d{4})-(\d{2})-(\d{2})[\\/]")

# una rafaga real de camara llega a ~10-20 fps, pero sostenido sobre miles de
# fotos es imposible: arriba de esto la fecha no puede ser de captura.
FOTOS_POR_MIN_IMPOSIBLE = 30.0


def pista_fecha(ruta):
    """(fecha_iso, origen) desde el nombre o la carpeta. (None, None) si no hay."""
    nombre = os.path.basename(ruta)
    m = RE_NOMBRE_DT.search(nombre)
    if m:
        a, me, d, h, mi, s = m.groups()
        if 1990 <= int(a) <= 2035 and 1 <= int(me) <= 12 and 1 <= int(d) <= 31:
            return f"{a}-{me}-{d}T{h}:{mi}:{s}", "nombre+hora"
    m = RE_CARPETA.search(ruta)
    if m:
        a, me, d = m.groups()
        if 1990 <= int(a) <= 2035 and 1 <= int(me) <= 12 and 1 <= int(d) <= 31:
            return f"{a}-{me}-{d}", "carpeta"
    m = RE_NOMBRE_D.search(nombre)
    if m:
        a, me, d = m.groups()
        if 1990 <= int(a) <= 2035 and 1 <= int(me) <= 12 and 1 <= int(d) <= 31:
            return f"{a}-{me}-{d}", "nombre"
    return None, None


def minutos(ini, fin):
    """Duracion en minutos entre dos ISO, o 0."""
    if not ini or not fin:
        return 0.0
    import datetime as dt
    try:
        a = dt.datetime.fromisoformat(ini)
        b = dt.datetime.fromisoformat(fin)
        return max((b - a).total_seconds() / 60.0, 0.0)
    except ValueError:
        return 0.0


def analizar(con):
    cur = con.cursor()
    filas = cur.execute("""
        SELECT ep.event_id, f.file_path, f.capture_date, f.camera_model, f.bytes
        FROM event_photos ep JOIN files f ON f.id = ep.photo_id""").fetchall()

    por_evento = defaultdict(list)
    for r in filas:
        por_evento[r["event_id"]].append(r)

    eventos = {r["id"]: r for r in cur.execute(
        "SELECT id, name, suggested_name, start_time, end_time, confirmado FROM events")}

    out = []
    for eid, fotos in por_evento.items():
        ev = eventos.get(eid)
        if not ev:
            continue
        fechas = sorted(f["capture_date"] for f in fotos if f["capture_date"])
        if not fechas:
            continue
        dur = minutos(fechas[0], fechas[-1])
        n = len(fotos)
        densidad = n / dur if dur > 0.5 else float(n)

        sin_exif = sum(1 for f in fotos if not f["camera_model"])
        contradicen = 0
        con_pista = 0
        for f in fotos:
            pista, _ = pista_fecha(f["file_path"])
            if not pista:
                continue
            con_pista += 1
            if pista[:10] != (f["capture_date"] or "")[:10]:
                contradicen += 1
        carpetas = len({os.path.dirname(f["file_path"]) for f in fotos})

        out.append({
            "id": eid,
            "nombre": (ev["name"] or ev["suggested_name"] or f"Evento {eid}"),
            "confirmado": ev["confirmado"],
            "fotos": n,
            "duracion_min": round(dur, 1),
            "fotos_por_min": round(densidad, 1),
            "sin_exif_pct": round(100.0 * sin_exif / n, 1),
            "con_pista": con_pista,
            "contradicen": contradicen,
            "contradicen_pct": round(100.0 * contradicen / con_pista, 1) if con_pista else 0.0,
            "carpetas": carpetas,
            "desde": fechas[0][:19],
            "hasta": fechas[-1][:19],
        })
    return out


def clasificar(e):
    """Etiqueta el problema principal, o None si el evento parece sano."""
    if e["fotos"] < 4:
        return None
    if e["contradicen_pct"] >= 80 and e["con_pista"] >= 4:
        return "FECHA FALSA (la ruta dice otra cosa)"
    if e["fotos_por_min"] >= FOTOS_POR_MIN_IMPOSIBLE and e["fotos"] >= 50:
        return "DENSIDAD IMPOSIBLE"
    if e["contradicen_pct"] >= 50 and e["con_pista"] >= 10:
        return "fecha dudosa (mitad contradicha)"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    datos = analizar(con)
    con.close()

    for e in datos:
        e["problema"] = clasificar(e) or ""

    malos = [e for e in datos if e["problema"]]
    malos.sort(key=lambda e: -e["fotos"])

    print(f"eventos analizados      : {len(datos):,}")
    print(f"fotos cubiertas         : {sum(e['fotos'] for e in datos):,}")
    print(f"eventos con problema    : {len(malos):,}")
    print(f"fotos en esos eventos   : {sum(e['fotos'] for e in malos):,}")
    print()
    for tipo, n in Counter(e["problema"] for e in malos).most_common():
        fotos = sum(e["fotos"] for e in malos if e["problema"] == tipo)
        print(f"  {n:5,} eventos  {fotos:8,} fotos   {tipo}")

    print()
    print("--- los 25 peores por cantidad de fotos ---")
    print(f"{'id':>6} {'fotos':>7} {'min':>8} {'f/min':>8} {'sinEXIF':>8} {'contra':>7}  nombre")
    for e in malos[:25]:
        print(f"{e['id']:>6} {e['fotos']:>7,} {e['duracion_min']:>8.1f} "
              f"{e['fotos_por_min']:>8.1f} {e['sin_exif_pct']:>7.0f}% "
              f"{e['contradicen_pct']:>6.0f}%  {e['nombre'][:44]}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(datos[0].keys()))
            w.writeheader()
            w.writerows(sorted(datos, key=lambda e: (not e["problema"], -e["fotos"])))
        print(f"\nCSV: {args.csv}")


if __name__ == "__main__":
    main()
