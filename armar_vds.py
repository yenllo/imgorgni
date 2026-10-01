r"""
Arma el Viaje de estudio a Italia 2025 con la estructura de la Liga:
un grupo con un evento por dia, nombrado segun el itinerario real.

Hoy el viaje esta en dos capas superpuestas que no son padre/hijo:
  - «Viaje de estudio - Italia 2025», que abarca 19 dias y tiene sobras
    sueltas en cada uno (450 fotos el 8, 252 el 9, 142 el 10...)
  - un evento por dia con nombre improvisado («VDS», «VDS Roma», «Laura, IT
    - monument»), que tiene el grueso de las fotos de ese dia.

Esto las junta: las fotos de cada dia van al evento de ese dia, el evento se
renombra con la ciudad y las actividades del itinerario, y queda colgado del
viaje. El viaje termina sin fotos propias, como «Liga ExScuola 2025-2».

El itinerario sale del Excel de Renzo (vds_itinerario.json, generado aparte).
Gracias a el se corrige un nombre equivocado: el evento «VDS bowling» del 20 de
enero era en realidad Venecia y Murano; el bowling fue el 26, en Florencia.

    python armar_vds.py            # informe, no toca nada
    python armar_vds.py --aplicar
"""
import argparse
import datetime as dt
import json
import sqlite3
import sys
from collections import defaultdict

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
ITINERARIO = "vds_itinerario.json"
PARAGUAS = "Viaje de estudio - Italia 2025"
MES = ["", "ene", "feb", "mar", "abr", "may", "jun",
       "jul", "ago", "sep", "oct", "nov", "dic"]
# Lo que no aporta nada al nombre de un dia
RUIDO = {"almuerzo", "cena", "tiempo libre", "do not map"}
# Typos del Excel que no conviene que terminen en el nombre de una carpeta
CORRIGE = {"Furenze": "Firenze", "Paertum": "Paestum"}


def nombre_del_dia(fecha, info):
    """«VDS 09 ene - Roma: Museo Vaticano, Capilla Sixtina»"""
    d = dt.date.fromisoformat(fecha)
    ciudades = "/".join(CORRIGE.get(x, x) for x in info.get("ciudades", [])) or "Italia"
    act = [a for a in info.get("act", [])
           if a.lower() not in RUIDO and a.strip().lower() not in
           {c.lower() for c in info.get("ciudades", [])}]
    cola = ", ".join(act[:2])
    base = f"VDS {d.day:02d} {MES[d.month]} - {ciudades}"
    return f"{base}: {cola}" if cola else base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()

    it = json.load(open(ITINERARIO, encoding="utf-8"))
    it = {k: v for k, v in it.items() if v.get("ciudades") or v.get("act")}
    c = sqlite3.connect(a.db, timeout=180)
    c.row_factory = sqlite3.Row

    fila = c.execute("SELECT id FROM events WHERE TRIM(name)=?", (PARAGUAS,)).fetchone()
    if not fila:
        sys.exit(f"no encuentro el evento «{PARAGUAS}»")
    paraguas = fila["id"]

    nombre = {r["id"]: (r["t"], bool(r["m"]))
              for r in c.execute("""SELECT id, COALESCE(NULLIF(TRIM(name),''),suggested_name) t,
                                    TRIM(COALESCE(name,''))<>'' m FROM events""")}
    por_dia = defaultdict(lambda: defaultdict(list))
    for eid, pid, cd in c.execute("""SELECT ep.event_id, f.id, f.capture_date
                                     FROM event_photos ep JOIN files f ON f.id=ep.photo_id
                                     WHERE COALESCE(f.borrado,0)=0 AND f.capture_date IS NOT NULL"""):
        por_dia[cd[:10]][eid].append(pid)

    plan = []
    for fecha in sorted(it):
        cuenta = por_dia.get(fecha, {})
        if not cuenta:
            continue
        candidatos = {e: p for e, p in cuenta.items() if e != paraguas}
        keeper = max(candidatos, key=lambda e: len(candidatos[e])) if candidatos else None
        donantes = [(e, p) for e, p in cuenta.items() if e != keeper]
        plan.append({"fecha": fecha, "keeper": keeper, "donantes": donantes,
                     "nuevo": nombre_del_dia(fecha, it[fecha]),
                     "fotos": sum(len(p) for p in cuenta.values())})

    print(f"«{PARAGUAS}» -> grupo con {len(plan)} dias\n")
    print("FECHA        FOTOS  AHORA                              QUEDA COMO")
    print("-" * 108)
    for p in plan:
        actual = nombre[p["keeper"]][0] if p["keeper"] else "(solo sobras del viaje)"
        extra = [nombre[e][0] for e, _ in p["donantes"] if e != paraguas]
        print(f"{p['fecha']}  {p['fotos']:5}  {actual[:32]:32}   {p['nuevo'][:56]}")
        for x in extra:
            print(f"{'':13}{'':6}  + absorbe «{x[:30]}»")

    huerfanos = sorted(set(por_dia) - set(it))
    tardios = [d for d in huerfanos if "2025-01-31" <= d <= "2025-02-28"]
    if tardios:
        print(f"\nfuera del itinerario (el viaje termina el 2025-01-30), NO los toco:")
        for d in tardios:
            for e in por_dia[d]:
                print(f"  {d}  {nombre[e][0][:40]}  ({len(por_dia[d][e])})")

    if not a.aplicar:
        print("\ndry-run: no se escribio nada. --aplicar para hacerlo.")
        return

    dest = f"{a.db}.backup-antes-armar-vds-{dt.datetime.now():%Y%m%d-%H%M%S}"
    o = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True, timeout=180)
    d = sqlite3.connect(dest)
    with d:
        o.backup(d)
    d.close()
    o.close()
    print(f"\nbackup: {dest}")

    for p in plan:
        keeper = p["keeper"]
        if keeper is None:            # el dia solo tiene sobras: nace su evento
            keeper = c.execute(
                "INSERT INTO events (name, suggested_name) VALUES (?,?)",
                (p["nuevo"], p["nuevo"])).lastrowid
        for e, pids in p["donantes"]:
            c.executemany("INSERT OR IGNORE INTO event_photos(event_id, photo_id) VALUES (?,?)",
                          [(keeper, x) for x in pids])
            c.executemany("DELETE FROM event_photos WHERE event_id=? AND photo_id=?",
                          [(e, x) for x in pids])
        rango = c.execute("""SELECT MIN(f.capture_date), MAX(f.capture_date)
                             FROM event_photos ep JOIN files f ON f.id=ep.photo_id
                             WHERE ep.event_id=?""", (keeper,)).fetchone()
        c.execute("""UPDATE events SET name=?, start_time=?, end_time=?, parent_id=?,
                                       coleccion=COALESCE(NULLIF(TRIM(coleccion),''),'VDS Italia 2025'),
                                       coleccion_tipo=COALESCE(coleccion_tipo,'viaje_de_estudio')
                     WHERE id=?""", (p["nuevo"], rango[0], rango[1], paraguas, keeper))

    # Los eventos que quedaron sin ninguna foto (donantes agotados) se van
    vacios = [r[0] for r in c.execute("""SELECT id FROM events WHERE id NOT IN
                (SELECT DISTINCT event_id FROM event_photos)
                AND id NOT IN (SELECT DISTINCT parent_id FROM events WHERE parent_id IS NOT NULL)""")]
    c.executemany("DELETE FROM events WHERE id=?", [(e,) for e in vacios])
    c.commit()
    n = c.execute("SELECT COUNT(*) FROM events WHERE parent_id=?", (paraguas,)).fetchone()[0]
    print(f"listo. «{PARAGUAS}» tiene {n} dias colgados; "
          f"{len(vacios)} eventos vacios eliminados")
    print(f"eventos en la base: {c.execute('SELECT COUNT(*) FROM events').fetchone()[0]:,}")


if __name__ == "__main__":
    main()
