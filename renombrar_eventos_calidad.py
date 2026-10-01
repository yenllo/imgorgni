r"""
Renombra los eventos que quedaron bautizados con un defecto de calidad.

El nombre automatico de un evento sale de la etiqueta de escena dominante, y
tres de esas etiquetas describen la CALIDAD en vez del contenido. Cuando ganan,
tapan de que es el evento: 507 fotos de una iglesia quedaron como 'completely
dark photo' — que es cierto, una iglesia es oscura, pero no dice nada.

Verificado el 2026-08-16 contra la calidad medida por foto: de los 74 eventos
llamados 'completely dark photo' solo 13 eran realmente oscuros (brillo < 20), y
de los 33 'blurry unusable photo' solo 3 eran borrosos (nitidez < 30). O sea que
el nombre no es un veredicto sobre las fotos: es una etiqueta mal elegida.

Este script les pone la etiqueta de CONTENIDO mas votada entre sus fotos. Los
que no tienen ninguna —todas sus etiquetas son de calidad— se dejan como estan:
esos si son los eventos genuinamente basura, y quedan faciles de encontrar.

Solo toca `suggested_name`, que es el nombre automatico. Nunca toca `name` (el
que ponés vos) ni los eventos con confirmado=1.

    python renombrar_eventos_calidad.py             # informe, no toca nada
    python renombrar_eventos_calidad.py --aplicar
"""
import argparse
import datetime as dt
import json
import sqlite3
import sys
from collections import Counter

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"

# Etiquetas que describen un defecto, no el contenido. 'black and white photo'
# queda AFUERA a proposito: es un estilo, no un problema.
CALIDAD = {"completely dark photo", "blurry unusable photo",
           "screenshot of a computer screen"}


def mejor_etiqueta(listas):
    """La etiqueta de contenido mas votada. None si todas son de calidad.

    Cada foto vota con TODAS sus etiquetas menos las de calidad; gana la que
    aparece en mas fotos. Empate: alfabetico, para que dos corridas den lo mismo.
    """
    c = Counter()
    for tags in listas:
        for t in tags:
            if isinstance(t, str) and t.lower() not in CALIDAD:
                c[t] += 1
    if not c:
        return None
    return min(c.items(), key=lambda kv: (-kv[1], kv[0]))[0]


def respaldar(db):
    dest = f"{db}.backup-antes-renombrar-calidad-{dt.datetime.now():%Y%m%d-%H%M%S}"
    o = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=180)
    d = sqlite3.connect(dest)
    with d:
        o.backup(d)
    d.close()
    o.close()
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()

    c = sqlite3.connect(a.db, timeout=180)
    c.row_factory = sqlite3.Row
    objetivo = {r["id"]: r["suggested_name"] for r in c.execute(
        """SELECT id, suggested_name FROM events
           WHERE COALESCE(confirmado,0)=0 AND TRIM(COALESCE(name,''))=''
             AND LOWER(COALESCE(suggested_name,'')) IN (%s)"""
        % ",".join("?" * len(CALIDAD)), sorted(CALIDAD))}
    print(f"eventos bautizados con un defecto de calidad: {len(objetivo)}")
    if not objetivo:
        return

    st = {p: s for p, s in c.execute(
        "SELECT photo_id, scene_tags FROM photo_features WHERE scene_tags IS NOT NULL")}
    por = {}
    for eid, pid in c.execute("SELECT event_id, photo_id FROM event_photos"):
        if eid in objetivo and pid in st:
            try:
                tags = json.loads(st[pid])
            except json.JSONDecodeError:
                continue
            if isinstance(tags, list):
                por.setdefault(eid, []).append(tags)

    cambios, sin_arreglo = [], []
    for eid, viejo in objetivo.items():
        nuevo = mejor_etiqueta(por.get(eid, []))
        (cambios if nuevo else sin_arreglo).append((eid, viejo, nuevo))

    print(f"  con etiqueta de contenido para reemplazar: {len(cambios)}")
    print(f"  sin ninguna (basura de verdad, se dejan): {len(sin_arreglo)}\n")

    tam = {r["id"]: r["k"] for r in c.execute(
        """SELECT id, (SELECT COUNT(*) FROM event_photos ep
                       WHERE ep.event_id=events.id) k FROM events""")}
    for eid, viejo, nuevo in sorted(cambios, key=lambda x: -tam.get(x[0], 0))[:15]:
        print(f"  [{tam.get(eid,0):4}] {viejo:32} -> {nuevo}")

    if not a.aplicar:
        print("\ndry-run: no se escribio nada. --aplicar para hacerlo.")
        return
    print(f"\nbackup: {respaldar(a.db)}")
    c.executemany("UPDATE events SET suggested_name=? WHERE id=?",
                  [(n, e) for e, _, n in cambios])
    c.commit()
    print(f"renombrados: {len(cambios)}")


if __name__ == "__main__":
    main()
