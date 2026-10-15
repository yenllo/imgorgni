"""Arma la jerarquia de la Liga ExScuola en tres niveles:

    Liga ExScuola                  <- evento raiz
      Liga ExScuola 2026-1         <- semestre
        F1 - 25-04-2026            <- fecha (aca viven las fotos)

Las fotos quedan siempre en la hoja (la fecha); los padres suman por
descendencia, asi que no se duplica ni una fila de event_photos.

Por defecto solo SIMULA. Para aplicar de verdad: python armar_liga.py --aplicar
(antes de escribir hace una copia de la base al lado, por las dudas).
"""
import argparse
import os
import sqlite3
import sys

from web_curador import DB

# Cada fecha: (etiqueta, dia_real, usar_fecha_captura, [patrones de carpeta])
# usar_fecha_captura=False cuando el reloj de la camara estaba reseteado (los
# DNG que aparecen como 2017-01-01) y la unica verdad es la carpeta.
SEMESTRES = [
    ("Liga ExScuola 2025-1", [
        ("26-04-2025", "2025-04-26", True,  []),
        ("10-05-2025", "2025-05-10", True,  []),
        ("17-05-2025", "2025-05-17", True,  []),
        ("24-05-2025", "2025-05-24", True,  []),
        ("31-05-2025", "2025-05-31", True,  []),
        ("07-06-2025", "2025-06-07", True,  []),
        ("14-06-2025", "2025-06-14", True,  []),
    ]),
    ("Liga ExScuola 2025-2", [
        # F3/F4/F5/F7: casi no sobrevivieron (confirmado con el chat del
        # organizador: hubo galeria entregada esas fechas, pero el material
        # de camara se perdio antes de llegar a esta biblioteca -- no es
        # corrupcion, no esta en cuarentena, no esta en ningun otro lado).
        # Quedan igual como sub-evento, aunque sea con pocas fotos.
        ("F1 - 30-08-2025", "2025-08-30", True, []),
        ("F2 - 13-09-2025", "2025-09-13", True, []),
        ("F3 - 27-09-2025", "2025-09-27", True, []),
        ("F4 - 04-10-2025", "2025-10-04", True, []),
        ("F5 - 11-10-2025", "2025-10-11", True, []),
        ("F6 - 18-10-2025", "2025-10-18", True, [r"%\fecha_6\%"]),
        ("F7 - 08-11-2025", "2025-11-08", True, []),
        ("F8 - 22-11-2025 (Final)", "2025-11-22", True, [r"%LigaExScuolaFinal%"]),
    ]),
    ("Liga ExScuola 2026-1", [
        # F1 y F2: reloj reseteado, van solo por carpeta.
        ("F1 - 25-04-2026", "2026-04-25", False,
         [r"%ExportacionFechas\F1.%", r"%LigaExScuola\2026\24-04-2026%"]),
        ("F2 - 09-05-2026", "2026-05-09", False,
         [r"%ExportacionFechas\F2.%", r"%LigaExScuola\2026\09-05-2026%"]),
        ("F3 - 16-05-2026", "2026-05-16", True, [r"%ExportacionFechas\F3.%"]),
        ("F4 - 30-05-2026", "2026-05-30", True, [r"%ExportacionFechas\F4.%"]),
        ("F5 - 06-06-2026", "2026-06-06", True, [r"%ExportacionFechas\F5.%"]),
        ("F6 - 13-06-2026", "2026-06-13", True, [r"%ExportacionFechas\F6.%"]),
        # La carpeta dice 21 pero el disparo fue el sabado 20.
        ("F7 - 20-06-2026", "2026-06-20", True,
         [r"%ExportacionFechas\F7.%", r"%LigaExScuola\2026\21-06-2026%"]),
        ("F8 - 04-07-2026", "2026-07-04", True, [r"%ExportacionFechas\F8.%"]),
        ("F9 - 11-07-2026 (Final)", "2026-07-11", True,
         [r"%ExportacionFechas\F9.%", r"%LigaExScuola\11-07-2026%"]),
    ]),
]

RAIZ = "Liga ExScuola"


def fotos_de_fecha(c, dia, usar_fecha, patrones):
    """IDs de las fotos de una fecha. El partido es de noche y a veces cruza la
    medianoche, asi que la ventana va del dia 00:00 hasta las 06:00 del siguiente."""
    ids = set()
    if usar_fecha:
        for (i,) in c.execute(
                """SELECT id FROM files
                   WHERE capture_date >= ? AND capture_date < datetime(?, '+30 hours')
                     AND COALESCE(dispositivo,'') <> 'telefono'
                     AND file_path NOT LIKE '%Telefonos_y_Capturas%'
                     AND file_path NOT LIKE '%_descartadas_movil_screenshot%'
                     AND COALESCE(borrado,0) = 0""", (dia, dia)):
            ids.add(i)
    for pat in patrones:
        for (i,) in c.execute(
                "SELECT id FROM files WHERE file_path LIKE ? AND COALESCE(borrado,0)=0",
                (pat,)):
            ids.add(i)
    return ids


def rango(c, ids, dia):
    """(desde, hasta) reales de un puñado de fotos. Si el reloj estaba roto
    (fechas 2017), se cae al horario nominal del partido."""
    if ids:
        marcas = [r[0] for r in c.execute(
            f"SELECT capture_date FROM files WHERE id IN ({','.join('?' * len(ids))})"
            " AND capture_date IS NOT NULL", tuple(ids)) if r[0] >= "2020"]
        if marcas:
            return min(marcas), max(marcas)
    return f"{dia}T19:00:00", f"{dia}T22:30:00"


def evento_para(c, etiqueta, ids):
    """Reutiliza el evento que ya contiene estas fotos (asi no se pierde nada de
    lo que ya curaste) o devuelve None si hay que crear uno nuevo."""
    if not ids:
        return None
    marcas = ",".join("?" * len(ids))
    cand = c.execute(
        f"""SELECT ep.event_id, COUNT(*) coinciden,
                   (SELECT COUNT(*) FROM event_photos WHERE event_id=ep.event_id) total
            FROM event_photos ep WHERE ep.photo_id IN ({marcas})
            GROUP BY ep.event_id ORDER BY coinciden DESC LIMIT 1""",
        tuple(ids)).fetchone()
    # Solo se reutiliza si el evento es *basicamente* esta fecha y no un cajon
    # con otras cosas mezcladas.
    if cand and cand[1] >= cand[2] * 0.8:
        return cand[0]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true",
                    help="escribe en la base (sin esto solo simula)")
    args = ap.parse_args()

    c = sqlite3.connect(DB, timeout=180)
    plan = []
    for semestre, fechas in SEMESTRES:
        sub = []
        for etiqueta, dia, usar_fecha, patrones in fechas:
            ids = fotos_de_fecha(c, dia, usar_fecha, patrones)
            sub.append((etiqueta, dia, ids, evento_para(c, etiqueta, ids)))
        plan.append((semestre, sub))

    print(f"{'':2}{'FECHA':<26}{'FOTOS':>8}  ACCION")
    total = 0
    for semestre, sub in plan:
        n_sem = sum(len(i) for _, _, i, _ in sub)
        total += n_sem
        print(f"\n{semestre}  ({n_sem:,} fotos, {len(sub)} fechas)")
        for etiqueta, dia, ids, reusa in sub:
            accion = f"reusa evento #{reusa}" if reusa else "crea evento nuevo"
            print(f"  {etiqueta:<26}{len(ids):>8,}  {accion}")
    print(f"\ntotal bajo «{RAIZ}»: {total:,} fotos")

    if not args.aplicar:
        print("\n(simulacion: no se escribio nada. Para aplicar: --aplicar)")
        return

    copia = DB.replace(".db", "_antes_de_liga.db")
    if not os.path.exists(copia):
        c.execute("VACUUM INTO ?", (copia,))
        print(f"\ncopia de seguridad: {copia}")

    raiz = c.execute("SELECT id FROM events WHERE name=?", (RAIZ,)).fetchone()
    if raiz:
        raiz = raiz[0]
    else:
        raiz = c.execute(
            "INSERT INTO events (name, suggested_name, confirmado) VALUES (?,?,1)",
            (RAIZ, RAIZ)).lastrowid

    for semestre, sub in plan:
        fila = c.execute("SELECT id FROM events WHERE name=?", (semestre,)).fetchone()
        sid = fila[0] if fila else c.execute(
            "INSERT INTO events (name, suggested_name, confirmado) VALUES (?,?,1)",
            (semestre, semestre)).lastrowid
        c.execute("UPDATE events SET parent_id=? WHERE id=?", (raiz, sid))

        for etiqueta, dia, ids, reusa in sub:
            if not ids:
                continue
            desde, hasta = rango(c, ids, dia)
            if reusa:
                eid = reusa
                c.execute("""UPDATE events SET name=?, start_time=?, end_time=?,
                                    parent_id=?, confirmado=1 WHERE id=?""",
                          (etiqueta, desde, hasta, sid, eid))
            else:
                eid = c.execute(
                    """INSERT INTO events (name, suggested_name, start_time,
                                           end_time, parent_id, confirmado)
                       VALUES (?,?,?,?,?,1)""",
                    (etiqueta, etiqueta, desde, hasta, sid)).lastrowid
            # Una foto, un evento: se saca de donde estuviera y queda en la fecha.
            c.executemany("DELETE FROM event_photos WHERE photo_id=?",
                          [(i,) for i in ids])
            c.executemany("INSERT OR IGNORE INTO event_photos (event_id, photo_id) VALUES (?,?)",
                          [(eid, i) for i in ids])

        r = c.execute("""SELECT MIN(start_time), MAX(end_time) FROM events
                         WHERE parent_id=?""", (sid,)).fetchone()
        c.execute("UPDATE events SET start_time=?, end_time=? WHERE id=?",
                  (r[0], r[1], sid))

    r = c.execute("SELECT MIN(start_time), MAX(end_time) FROM events WHERE parent_id=?",
                  (raiz,)).fetchone()
    c.execute("UPDATE events SET start_time=?, end_time=? WHERE id=?", (r[0], r[1], raiz))

    # Eventos que quedaron sin una sola foto y sin hijos: sobraron del clustering.
    vacios = c.execute(
        """SELECT id, COALESCE(NULLIF(name,''),suggested_name) FROM events
           WHERE NOT EXISTS (SELECT 1 FROM event_photos WHERE event_id=events.id)
             AND NOT EXISTS (SELECT 1 FROM events h WHERE h.parent_id=events.id)"""
    ).fetchall()
    for eid, nom in vacios:
        c.execute("DELETE FROM events WHERE id=?", (eid,))
    c.commit()
    print(f"\nlisto. eventos vacios eliminados: {len(vacios)}")
    for eid, nom in vacios[:15]:
        print(f"    #{eid} {nom!r}")
    c.close()


if __name__ == "__main__":
    main()
