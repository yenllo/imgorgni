r"""
Fusiona un evento suelto sin nombre dentro del evento nombrado del mismo dia.

La Fase 3 partia cada jornada de la Liga en dos: la que Renzo nombro y colgo del
arbol de la temporada, y un gemelo suelto que quedo como 'playing soccer'. Eran
11 gemelos con 14.584 fotos. `limpiar_eventos.py` nunca los toco a proposito
—excluye los confirmados y los que cuelgan de un padre— asi que hace falta esto.

El que manda es el evento NOMBRADO: conserva su nombre, su padre, su fecha y su
marca de confirmado. El gemelo aporta sus fotos y desaparece.

No se recalculan start_time/end_time: en un evento curado a mano esas fechas
pueden estar corregidas, y las fotos que entran son del mismo dia igual.

    python fusionar_gemelos.py --plan fusion_futbol.json
    python fusionar_gemelos.py --plan fusion_futbol.json --aplicar

El plan es un JSON [[id_suelto, id_destino], ...].
"""
import argparse
import datetime as dt
import json
import sqlite3
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"


def respaldar(db):
    dest = f"{db}.backup-antes-fusionar-gemelos-{dt.datetime.now():%Y%m%d-%H%M%S}"
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
    ap.add_argument("--plan", required=True)
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()

    plan = json.load(open(a.plan, encoding="utf-8"))
    c = sqlite3.connect(a.db, timeout=180)
    c.row_factory = sqlite3.Row

    # Que el plan siga siendo valido: los eventos existen y el destino tiene
    # nombre a mano. Si algo cambio desde que se calculo, se corta.
    for suelto, destino in plan:
        for eid, quien in ((suelto, "suelto"), (destino, "destino")):
            if not c.execute("SELECT 1 FROM events WHERE id=?", (eid,)).fetchone():
                sys.exit(f"el evento {quien} {eid} ya no existe: recalcula el plan")
        if not (c.execute("SELECT TRIM(COALESCE(name,'')) FROM events WHERE id=?",
                          (destino,)).fetchone()[0]):
            sys.exit(f"el destino {destino} no tiene nombre a mano: recalcula el plan")

    total = sum(c.execute("SELECT COUNT(*) FROM event_photos WHERE event_id=?",
                          (s,)).fetchone()[0] for s, _ in plan)
    print(f"gemelos a fusionar: {len(plan)}   fotos que se mueven: {total:,}")
    if not a.aplicar:
        print("dry-run: no se escribio nada. --aplicar para hacerlo.")
        return

    print(f"backup: {respaldar(a.db)}")
    movidas = 0
    for suelto, destino in plan:
        antes = c.execute("SELECT COUNT(*) FROM event_photos WHERE event_id=?",
                          (destino,)).fetchone()[0]
        c.execute("""INSERT OR IGNORE INTO event_photos(event_id, photo_id)
                     SELECT ?, photo_id FROM event_photos WHERE event_id=?""",
                  (destino, suelto))
        c.execute("DELETE FROM event_photos WHERE event_id=?", (suelto,))
        # foreign_keys=0: el borrado de event_photos va explicito, arriba.
        c.execute("DELETE FROM events WHERE id=?", (suelto,))
        despues = c.execute("SELECT COUNT(*) FROM event_photos WHERE event_id=?",
                            (destino,)).fetchone()[0]
        movidas += despues - antes
    c.commit()
    print(f"listo. {movidas:,} fotos sumadas, {len(plan)} eventos sueltos eliminados")
    print(f"eventos en la base: {c.execute('SELECT COUNT(*) FROM events').fetchone()[0]:,}")


if __name__ == "__main__":
    main()
