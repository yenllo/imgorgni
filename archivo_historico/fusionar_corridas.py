"""Fusiona eventos top-level que caen en dias consecutivos (o con salto de un
solo dia) en uno solo. Ya se fusiono por mismo-dia antes; esto agarra lo que
queda: una foto de "viaje" real casi siempre se reparte en varios dias
seguidos, y quedaba partida en un evento por dia.

No toca sub-eventos (Liga ExScuola y sus fechas). Requiere 3+ dias en la
corrida para fusionar (2 dias consecutivos sueltos son mas ambiguos, se dejan
para que el usuario los revise a mano).

Por defecto SIMULA. Para aplicar: python fusionar_corridas.py --aplicar
"""
import argparse
import datetime
import sqlite3

from web_curador import DB


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--min-dias", type=int, default=3)
    args = ap.parse_args()

    c = sqlite3.connect(DB, timeout=180)
    rows = c.execute("""
        SELECT e.id, COALESCE(NULLIF(e.name,''),e.suggested_name) nom,
               substr(e.start_time,1,10) d,
               (SELECT COUNT(*) FROM event_photos WHERE event_id=e.id) n
        FROM events e WHERE e.parent_id IS NULL AND e.start_time IS NOT NULL
        ORDER BY e.start_time
    """).fetchall()

    corridas, actual = [], []
    for eid, nom, d, n in rows:
        fecha = datetime.date.fromisoformat(d)
        if actual and (fecha - datetime.date.fromisoformat(actual[-1][2])).days <= 1:
            actual.append((eid, nom, d, n))
        else:
            if len(actual) >= args.min_dias:
                corridas.append(actual)
            actual = [(eid, nom, d, n)]
    if len(actual) >= args.min_dias:
        corridas.append(actual)

    print(f"corridas de {args.min_dias}+ dias: {len(corridas)}")
    total_fusionados = 0

    for corrida in corridas:
        ids = [x[0] for x in corrida]
        filas = c.execute(
            f"SELECT id, name, suggested_name, confirmado, notas, viaje, coleccion "
            f"FROM events WHERE id IN ({','.join('?'*len(ids))})", ids).fetchall()

        con_nombre = [f for f in filas if (f[1] or "").strip()]
        if con_nombre:
            sobrevive = con_nombre[0][0]
        else:
            conteos = {i: c.execute(
                "SELECT COUNT(*) FROM event_photos WHERE event_id=?", (i,)
            ).fetchone()[0] for i in ids}
            sobrevive = max(conteos, key=conteos.get)

        perdedores = [i for i in ids if i != sobrevive]
        confirmado = any(f[3] for f in filas)
        notas = " / ".join(f[4] for f in filas if f[4])
        viaje = next((f[5] for f in filas if f[5]), None)
        coleccion = next((f[6] for f in filas if f[6]), None)

        d0, d1 = corrida[0][2], corrida[-1][2]
        print(f"  {d0}..{d1} ({len(corrida)} dias) -> sobrevive #{sobrevive}, "
              f"absorbe {perdedores}")

        if args.aplicar:
            for pid in perdedores:
                c.execute("""INSERT OR IGNORE INTO event_photos (event_id, photo_id)
                             SELECT ?, photo_id FROM event_photos WHERE event_id=?""",
                          (sobrevive, pid))
                c.execute("DELETE FROM events WHERE id=?", (pid,))
            r = c.execute("""SELECT MIN(f.capture_date), MAX(f.capture_date)
                             FROM event_photos ep JOIN files f ON f.id=ep.photo_id
                             WHERE ep.event_id=?""", (sobrevive,)).fetchone()
            c.execute("""UPDATE events SET start_time=?, end_time=?, confirmado=?,
                                notas=?, viaje=COALESCE(viaje,?), coleccion=COALESCE(coleccion,?)
                         WHERE id=?""",
                      (r[0] or d0, r[1] or d1, confirmado, notas or None,
                       viaje, coleccion, sobrevive))
        total_fusionados += len(perdedores)

    if args.aplicar:
        c.commit()
    print(f"\ntotal eventos absorbidos: {total_fusionados}")
    if not args.aplicar:
        print("(simulacion: no se escribio nada. Para aplicar: --aplicar)")
    c.close()


if __name__ == "__main__":
    main()
