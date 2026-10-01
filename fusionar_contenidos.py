r"""
Fusiona los eventos cuyo rango de tiempo está CONTENIDO dentro de otro.

El clustering parte por parecido visual dentro de un mismo rango temporal: en un
partido de 19:03 a 23:02, las fotos de la tribuna se separan en su propio
"evento" de 21:35 a 21:56. Pero un evento es algo de tiempo y lugar, no de
escena: si B ocurre íntegramente dentro de A, B es parte de A.

Cuida dos cosas:
  - No toca lo que curaste a mano (confirmado, notas, colección, nombre propio).
  - Si el chico tiene nombre de OCR ("Liga Ex-Scuola") y el grande tiene una
    etiqueta genérica de escena ("sports game"), se queda el nombre bueno.

    python fusionar_contenidos.py                  # simula y muestra
    python fusionar_contenidos.py --aplicar        # escribe
"""
import argparse
import sqlite3
import sys
from collections import defaultdict

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"


def curado(e):
    """True si vos tocaste este evento: no se fusiona sin permiso."""
    return bool(e["confirmado"] or (e["notas"] or "").strip()
                or (e["coleccion"] or "").strip() or (e["viaje"] or "").strip())


def titulo(e):
    return (e["name"] or "").strip() or (e["suggested_name"] or "").strip() or f"#{e['id']}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    c = sqlite3.connect(args.db, timeout=120)
    c.row_factory = sqlite3.Row
    evs = [dict(r) for r in c.execute("""
        SELECT e.*, COUNT(ep.photo_id) n FROM events e
        LEFT JOIN event_photos ep ON ep.event_id = e.id
        WHERE e.start_time IS NOT NULL AND e.end_time IS NOT NULL
        GROUP BY e.id""")]
    print(f"eventos con rango horario: {len(evs):,}")

    # Para cada evento, el contenedor MÁS GRANDE que lo contenga por completo.
    destino = {}
    for b in evs:
        mejor = None
        for a in evs:
            if a["id"] == b["id"]:
                continue
            if a["start_time"] <= b["start_time"] and b["end_time"] <= a["end_time"]:
                # empate de rangos idénticos: gana el que tiene más fotos
                if (a["n"], a["id"]) <= (b["n"], b["id"]):
                    continue
                if mejor is None or a["n"] > mejor["n"]:
                    mejor = a
        if mejor:
            destino[b["id"]] = mejor["id"]

    # A contiene a B y B contiene a C -> C va al más externo
    def raiz(i, visto=None):
        visto = visto or set()
        while i in destino and i not in visto:
            visto.add(i)
            i = destino[i]
        return i

    por_id = {e["id"]: e for e in evs}
    fusiones = defaultdict(list)
    saltados = []
    for b_id in destino:
        b = por_id[b_id]
        if curado(b):
            saltados.append(b)
            continue
        r = raiz(b_id)
        if r != b_id:
            fusiones[r].append(b)

    total = sum(len(v) for v in fusiones.values())
    print(f"a fusionar: {total:,} eventos dentro de {len(fusiones):,} contenedores")
    if saltados:
        print(f"respetados por estar curados a mano: {len(saltados):,}")

    print("\nejemplos:")
    for a_id, hijos in list(fusiones.items())[:6]:
        a = por_id[a_id]
        print(f"  {titulo(a)}  ({a['start_time'][11:19]}→{a['end_time'][11:19]}, {a['n']:,} fotos)")
        for h in hijos[:4]:
            print(f"     ← {titulo(h):<24} {h['start_time'][11:19]}→{h['end_time'][11:19]}  {h['n']:>5} fotos")

    if not args.aplicar:
        print("\nSimulación. Para escribir:  python fusionar_contenidos.py --aplicar")
        return

    movidas = renombrados = 0
    for a_id, hijos in fusiones.items():
        a = por_id[a_id]
        for h in hijos:
            c.execute("UPDATE OR IGNORE event_photos SET event_id=? WHERE event_id=?",
                      (a_id, h["id"]))
            c.execute("DELETE FROM event_photos WHERE event_id=?", (h["id"],))
            movidas += h["n"]
            # el nombre del OCR vale más que una etiqueta de escena
            if h["ocr_pista"] and not a["ocr_pista"]:
                c.execute("UPDATE events SET name=?, ocr_pista=? WHERE id=?",
                          (titulo(h), h["ocr_pista"], a_id))
                a["ocr_pista"] = h["ocr_pista"]
                renombrados += 1
            c.execute("DELETE FROM events WHERE id=?", (h["id"],))
        r = c.execute("""SELECT MIN(f.capture_date) a, MAX(f.capture_date) b
                         FROM event_photos ep JOIN files f ON f.id=ep.photo_id
                         WHERE ep.event_id=?""", (a_id,)).fetchone()
        if r and r["a"]:
            c.execute("UPDATE events SET start_time=?, end_time=? WHERE id=?",
                      (r["a"], r["b"], a_id))
    c.commit()
    n = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    print(f"\n✅ {total:,} eventos fusionados ({movidas:,} fotos reubicadas)")
    if renombrados:
        print(f"   {renombrados} contenedores adoptaron el nombre del OCR del chico")
    print(f"   eventos ahora: {n:,}")
    c.close()


if __name__ == "__main__":
    main()
