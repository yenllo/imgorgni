r"""
Un dia = un evento. Absorbe los eventos de un solo dia dentro del evento
principal de ese dia.

La Fase 3 cortaba por CONTENIDO dentro de la ventana temporal, asi que un mismo
dia quedaba repartido: el 23/1/2025 tenia 6 eventos ('Turin, IT VDS Torino',
'museum', 'historic building', 'museum'...). Medido el 2026-08-16: 150 de 442
dias con fotos tenian mas de un evento, con 66.032 fotos involucradas.

REGLAS, en orden:

  1. Si el dia tiene DOS O MAS eventos con nombre puesto a mano, no se toca:
     esa division la hizo Renzo a proposito (ej. 'Scuola concert' y
     'Scuola concert y cierre' el mismo dia).
  2. El que manda es el evento con nombre a mano; si no hay ninguno, el que
     tiene mas fotos ese dia.
  3. Solo se absorben eventos de UN SOLO DIA. Un evento que abarca varios dias
     —un viaje, una Settimana— es una estructura deliberada y nunca se disuelve
     por coincidir un dia con otro.
  4. Nunca se absorbe algo con nombre a mano, confirmado, con padre o con hijos.

No se recalculan start_time/end_time del que manda: puede tener la fecha
corregida a mano y las fotos que entran son del mismo dia igual.

    python fusionar_por_dia.py                 # informe, no toca nada
    python fusionar_por_dia.py --aplicar
"""
import argparse
import datetime as dt
import sqlite3
import sys
from collections import defaultdict

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"


def respaldar(db):
    dest = f"{db}.backup-antes-fusionar-por-dia-{dt.datetime.now():%Y%m%d-%H%M%S}"
    o = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=180)
    d = sqlite3.connect(dest)
    with d:
        o.backup(d)
    d.close()
    o.close()
    return dest


def planear(c):
    """[(dia, keeper, [absorbidos])] segun las reglas del encabezado."""
    ev = {r[0]: {"nombre": r[1], "manual": bool(r[2]), "confirmado": bool(r[3]),
                 "padre": r[4]}
          for r in c.execute("""SELECT id, COALESCE(NULLIF(TRIM(name),''),suggested_name),
                                TRIM(COALESCE(name,''))<>'', COALESCE(confirmado,0), parent_id
                                FROM events""")}
    con_hijos = {r[0] for r in c.execute(
        "SELECT DISTINCT parent_id FROM events WHERE parent_id IS NOT NULL")}

    dias_de = defaultdict(set)          # evento -> dias que abarca
    en_dia = defaultdict(lambda: defaultdict(int))   # dia -> evento -> fotos
    for eid, cd in c.execute("""SELECT ep.event_id, f.capture_date
                                FROM event_photos ep JOIN files f ON f.id=ep.photo_id
                                WHERE COALESCE(f.borrado,0)=0 AND f.capture_date IS NOT NULL"""):
        d = cd[:10]
        dias_de[eid].add(d)
        en_dia[d][eid] += 1

    plan, saltados = [], []
    for dia, cuenta in en_dia.items():
        if len(cuenta) < 2:
            continue
        manuales = [e for e in cuenta if ev[e]["manual"]]
        if len(manuales) > 1:                       # regla 1
            saltados.append((dia, manuales))
            continue
        keeper = manuales[0] if manuales else max(cuenta, key=lambda e: cuenta[e])
        absorbidos = [e for e in cuenta
                      if e != keeper
                      and len(dias_de[e]) == 1                     # regla 3
                      and not ev[e]["manual"] and not ev[e]["confirmado"]
                      and ev[e]["padre"] is None and e not in con_hijos]  # regla 4
        if absorbidos:
            plan.append((dia, keeper, absorbidos))
    return plan, saltados, ev, en_dia


def planear_sobras(c):
    """Modo --sobras: mueve FOTOS, no eventos.

    Cuando un dia tiene un evento principal con nombre y al lado sobras sin
    nombre ('museum', 'bar', 'motorcycle'), esas sobras son del mismo momento y
    el agrupador las separo por contenido. Se mueven las fotos DE ESE DIA al
    principal; si el evento donante queda vacio, se borra, y si abarcaba otros
    dias sobrevive con lo que le queda.

    Los donantes con nombre a mano NO se tocan nunca: ahi entra el paraguas de
    un viaje ('Viaje de estudio - Italia 2025', 19 dias), que no es una sobra
    sino la estructura del viaje y va resuelto colgandolo como grupo.

    Devuelve [(dia, keeper, [(donante, [photo_id...])])].
    """
    ev = {r[0]: {"nombre": r[1], "manual": bool(r[2]), "confirmado": bool(r[3]),
                 "padre": r[4]}
          for r in c.execute("""SELECT id, COALESCE(NULLIF(TRIM(name),''),suggested_name),
                                TRIM(COALESCE(name,''))<>'', COALESCE(confirmado,0), parent_id
                                FROM events""")}
    con_hijos = {r[0] for r in c.execute(
        "SELECT DISTINCT parent_id FROM events WHERE parent_id IS NOT NULL")}

    porf = defaultdict(lambda: defaultdict(list))     # dia -> evento -> [pid]
    for eid, pid, cd in c.execute("""SELECT ep.event_id, f.id, f.capture_date
                                     FROM event_photos ep JOIN files f ON f.id=ep.photo_id
                                     WHERE COALESCE(f.borrado,0)=0
                                       AND f.capture_date IS NOT NULL"""):
        porf[cd[:10]][eid].append(pid)

    plan = []
    for dia, cuenta in porf.items():
        if len(cuenta) < 2:
            continue
        # El principal del dia: con nombre a mano y el mas grande de ese dia.
        nombrados = [e for e in cuenta if ev[e]["manual"]]
        if nombrados:
            keeper = max(nombrados, key=lambda e: len(cuenta[e]))
        else:
            keeper = max(cuenta, key=lambda e: len(cuenta[e]))
        donantes = [(e, cuenta[e]) for e in cuenta
                    if e != keeper and not ev[e]["manual"]
                    and not ev[e]["confirmado"]
                    and ev[e]["padre"] is None and e not in con_hijos]
        if donantes:
            plan.append((dia, keeper, donantes))
    return plan, ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--sobras", action="store_true",
                    help="mueve las fotos sueltas del dia al evento principal")
    a = ap.parse_args()

    c = sqlite3.connect(a.db, timeout=180)
    if a.sobras:
        return modo_sobras(c, a)
    plan, saltados, ev, en_dia = planear(c)
    n_abs = sum(len(x[2]) for x in plan)
    total = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    print(f"dias a fusionar          : {len(plan)}")
    print(f"eventos que desaparecen  : {n_abs}   ({total} -> {total - n_abs})")
    print(f"dias intocados (2+ nombrados a mano): {len(saltados)}")

    print("\nlos 12 dias mas partidos:")
    for dia, keeper, abs_ in sorted(plan, key=lambda x: -len(x[2]))[:12]:
        print(f"  {dia}  «{ev[keeper]['nombre'][:34]}» absorbe {len(abs_)}: "
              + ", ".join(f"{ev[e]['nombre'][:18]}({en_dia[dia][e]})" for e in abs_[:4])
              + (" …" if len(abs_) > 4 else ""))
    if saltados:
        print("\ndias que NO toco (los dividiste vos):")
        for dia, ms in saltados[:8]:
            print(f"  {dia}: " + " | ".join(ev[e]["nombre"][:30] for e in ms))

    if not a.aplicar:
        print("\ndry-run: no se escribio nada. --aplicar para hacerlo.")
        return

    print(f"\nbackup: {respaldar(a.db)}")
    movidas = 0
    for _dia, keeper, absorbidos in plan:
        for e in absorbidos:
            antes = c.execute("SELECT COUNT(*) FROM event_photos WHERE event_id=?",
                              (keeper,)).fetchone()[0]
            c.execute("""INSERT OR IGNORE INTO event_photos(event_id, photo_id)
                         SELECT ?, photo_id FROM event_photos WHERE event_id=?""",
                      (keeper, e))
            # Lo que el absorbido sepa y el que manda no, se conserva: notas,
            # viaje y coleccion son datos que puso una persona o el OCR, y no
            # estan en las fotos. (Esto venia de la version anterior del script.)
            c.execute("""UPDATE events SET
                           notas     = COALESCE(NULLIF(TRIM(notas),''),
                                                (SELECT NULLIF(TRIM(notas),'') FROM events WHERE id=:v)),
                           viaje     = COALESCE(viaje, (SELECT viaje FROM events WHERE id=:v)),
                           coleccion = COALESCE(coleccion, (SELECT coleccion FROM events WHERE id=:v)),
                           coleccion_tipo = COALESCE(coleccion_tipo,
                                                (SELECT coleccion_tipo FROM events WHERE id=:v))
                         WHERE id=:k""", {"v": e, "k": keeper})
            # foreign_keys=0: el borrado de event_photos va explicito
            c.execute("DELETE FROM event_photos WHERE event_id=?", (e,))
            c.execute("DELETE FROM events WHERE id=?", (e,))
            movidas += c.execute("SELECT COUNT(*) FROM event_photos WHERE event_id=?",
                                 (keeper,)).fetchone()[0] - antes
    c.commit()
    print(f"listo. {movidas:,} fotos reunidas, {n_abs} eventos absorbidos")
    print(f"eventos en la base: {c.execute('SELECT COUNT(*) FROM events').fetchone()[0]:,}")


def modo_sobras(c, a):
    plan, ev = planear_sobras(c)
    fotos = sum(len(p) for _d, _k, ds in plan for _e, p in ds)
    donantes = {e for _d, _k, ds in plan for e, _p in ds}
    print(f"dias con sobras sin nombre : {len(plan)}")
    print(f"fotos que se reunen        : {fotos:,}")
    print(f"eventos donantes           : {len(donantes)}")

    print("\nlos 12 dias con mas sobras:")
    for dia, keeper, ds in sorted(plan, key=lambda x: -sum(len(p) for _e, p in x[2]))[:12]:
        detalle = ", ".join(f"{ev[e]['nombre'][:20]}({len(p)})" for e, p in ds[:4])
        print(f"  {dia}  «{ev[keeper]['nombre'][:32]}» recibe "
              f"{sum(len(p) for _e, p in ds):4} de: {detalle}"
              + (" …" if len(ds) > 4 else ""))

    if not a.aplicar:
        print("\ndry-run: no se escribio nada. --aplicar --sobras para hacerlo.")
        return

    print(f"\nbackup: {respaldar(a.db)}")
    movidas = 0
    for _dia, keeper, ds in plan:
        for e, pids in ds:
            c.executemany("INSERT OR IGNORE INTO event_photos(event_id, photo_id) VALUES (?,?)",
                          [(keeper, p) for p in pids])
            c.executemany("DELETE FROM event_photos WHERE event_id=? AND photo_id=?",
                          [(e, p) for p in pids])
            movidas += len(pids)
    # Los donantes que quedaron sin ninguna foto ya no son nada
    vacios = [e for e in {e for _d, _k, ds in plan for e, _p in ds}
              if not c.execute("SELECT 1 FROM event_photos WHERE event_id=? LIMIT 1",
                               (e,)).fetchone()]
    c.executemany("DELETE FROM events WHERE id=?", [(e,) for e in vacios])
    c.commit()
    print(f"listo. {movidas:,} fotos reunidas, {len(vacios)} eventos quedaron vacios y se borraron")
    print(f"eventos en la base: {c.execute('SELECT COUNT(*) FROM events').fetchone()[0]:,}")


if __name__ == "__main__":
    main()
