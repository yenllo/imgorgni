r"""
Limpia el arbol de eventos: saca lo que no es camara, borra los que quedan
vacios y fusiona los que son el mismo momento partido en pedazos.

Las tres cosas son el mismo problema. La Fase 3 agrupa por CONTENIDO dentro de
una ventana temporal, asi que una sesion seguida se parte en 'bar', 'garden',
'plate of food'. Y como el filtro de movil nunca corrio (enable_mobile_filter
esta en false), las fotos de telefono entraron al clustering y se armaron sus
propios eventos: 2.417 de 3.111 eventos no tienen NI UNA foto de camara.

Sacando el telefono primero, el 78% de los eventos desaparece solo. Lo que
queda para fusionar es chico y se puede mirar.

    python limpiar_eventos.py                  # informe, no toca nada
    python limpiar_eventos.py --aplicar        # hace backup y escribe
    python limpiar_eventos.py --umbral 60      # fusiona con hueco de 60 min

Nunca toca:
  - los archivos en disco (solo desvincula filas de event_photos)
  - los eventos con confirmado=1 (los reviso a mano, mando yo)
  - photos_v2.db sin backup previo
"""
import argparse
import datetime as dt
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, ".")
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from utils_analisis import clasificar_dispositivo

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"


def parse(s):
    try:
        return dt.datetime.fromisoformat(s[:19].replace(" ", "T"))
    except (ValueError, TypeError, AttributeError):
        return None


def planear_fusiones(eventos, umbral_min):
    """[(eid, ini, fin, n)] ordenados por ini -> [[eid, eid, ...], ...].

    Encadena: si A pega con B y B con C, los tres van juntos. Un hueco negativo
    (se solapan en el tiempo) es la senal mas fuerte de que es el mismo momento.
    Funcion pura para poder testearla; ver test_limpiar_eventos.py.
    """
    grupos = []
    for eid, ini, fin, n in sorted(eventos, key=lambda e: e[1]):
        if grupos:
            g = grupos[-1]
            if (ini - g["fin"]).total_seconds() / 60 <= umbral_min:
                g["ids"].append(eid)
                g["fin"] = max(g["fin"], fin)
                continue
        grupos.append({"ids": [eid], "ini": ini, "fin": fin})
    return [g["ids"] for g in grupos if len(g["ids"]) > 1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--aplicar", action="store_true", help="escribir (por defecto: informe)")
    ap.add_argument("--umbral", type=float, default=30.0, help="minutos de hueco para fusionar")
    ap.add_argument("--sacar-desconocido", action="store_true",
                    help="tambien desvincula las sin EXIF (son exports web de camara)")
    a = ap.parse_args()

    if a.aplicar:
        # La base esta en WAL y el curador la tiene abierta: copiar el .db suelto
        # deja afuera lo que todavia vive en el -wal. La API backup de sqlite
        # toma una foto consistente aunque haya otro proceso escribiendo.
        dest = f"{a.db}.backup-antes-limpiar-eventos-{dt.datetime.now():%Y%m%d-%H%M%S}"
        origen = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True, timeout=180)
        destino = sqlite3.connect(dest)
        with destino:
            origen.backup(destino)
        destino.close()
        origen.close()
        print(f"backup: {dest}\n")

    c = sqlite3.connect(a.db, timeout=180)
    fuera = {"telefono", "desconocido"} if a.sacar_desconocido else {"telefono"}

    disp, fecha = {}, {}
    for pid, fp, cm, cd in c.execute(
            "SELECT id, file_path, camera_model, capture_date FROM files "
            "WHERE COALESCE(borrado,0)=0"):
        disp[pid] = clasificar_dispositivo(cm, fp)
        fecha[pid] = parse(cd)

    protegidos = {r[0] for r in c.execute("SELECT id FROM events WHERE confirmado=1")}
    padres = {r[0] for r in c.execute(
        "SELECT DISTINCT parent_id FROM events WHERE parent_id IS NOT NULL")}
    hijos = {r[0] for r in c.execute(
        "SELECT id FROM events WHERE parent_id IS NOT NULL")}
    intocables = protegidos | padres | hijos

    # ---- 1. desvincular lo que no es camara -----------------------------
    sacar = [(eid, pid) for eid, pid in c.execute("SELECT event_id, photo_id FROM event_photos")
             if eid not in protegidos and disp.get(pid, "desconocido") in fuera]
    print(f"1. fotos que no son camara dentro de eventos: {len(sacar):,}")
    if a.aplicar:
        c.executemany("DELETE FROM event_photos WHERE event_id=? AND photo_id=?", sacar)

    # Estado resultante (en memoria si es dry-run, para poder seguir el plan)
    quitados = set(sacar)
    vivos = defaultdict(list)
    for eid, pid in c.execute("SELECT event_id, photo_id FROM event_photos"):
        if (eid, pid) in quitados or pid not in disp:
            continue
        vivos[eid].append(pid)

    # ---- 2. borrar los que quedaron vacios -------------------------------
    todos = [r[0] for r in c.execute("SELECT id FROM events")]
    vacios = [e for e in todos if not vivos.get(e) and e not in intocables]
    print(f"2. eventos que quedan sin ninguna foto: {len(vacios):,}")
    if a.aplicar:
        # foreign_keys esta en 0, o sea que ON DELETE CASCADE no corre: hay que
        # limpiar event_photos a mano o quedan filas apuntando a un evento muerto.
        c.executemany("DELETE FROM event_photos WHERE event_id=?", [(e,) for e in vacios])
        c.executemany("DELETE FROM events WHERE id=?", [(e,) for e in vacios])

    # ---- 3. fusionar los pedazos del mismo momento -----------------------
    cand = []
    for eid, pids in vivos.items():
        if eid in intocables:
            continue
        fs = sorted(f for f in (fecha.get(p) for p in pids) if f)
        if fs:
            cand.append((eid, fs[0], fs[-1], len(fs)))
    grupos = planear_fusiones(cand, a.umbral)
    n_ev = sum(len(g) for g in grupos)
    print(f"3. eventos sobrevivientes: {len(cand):,}  ->  "
          f"{n_ev:,} se fusionan en {len(grupos):,} (hueco <= {a.umbral:g} min)")

    tam = {e: n for e, _, _, n in cand}
    for ids in grupos:
        # se queda el que mas fotos tiene; si alguno tiene nombre a mano, ese nombre
        keeper = max(ids, key=lambda e: tam[e])
        nombres = [r for r in c.execute(
            f"SELECT id, name FROM events WHERE id IN ({','.join('?' * len(ids))})", ids)
            if (r[1] or "").strip()]
        if a.aplicar:
            resto = [e for e in ids if e != keeper]
            for e in resto:
                c.execute("INSERT OR IGNORE INTO event_photos(event_id, photo_id) "
                          "SELECT ?, photo_id FROM event_photos WHERE event_id=?", (keeper, e))
                c.execute("DELETE FROM event_photos WHERE event_id=?", (e,))
                c.execute("DELETE FROM events WHERE id=?", (e,))
            if nombres and not (c.execute("SELECT name FROM events WHERE id=?",
                                          (keeper,)).fetchone()[0] or "").strip():
                c.execute("UPDATE events SET name=? WHERE id=?", (nombres[0][1], keeper))

    # ---- 4. reajustar start_time/end_time a las fotos que quedaron -------
    # Los confirmados quedan afuera: ahi la fecha puede estar corregida a mano
    # desde el curador, y recalcularla de las fotos la pisaria.
    if a.aplicar:
        c.execute("""UPDATE events SET
            start_time = COALESCE((SELECT MIN(f.capture_date) FROM event_photos ep
                JOIN files f ON f.id=ep.photo_id WHERE ep.event_id=events.id
                AND COALESCE(f.borrado,0)=0 AND f.capture_date IS NOT NULL), start_time),
            end_time = COALESCE((SELECT MAX(f.capture_date) FROM event_photos ep
                JOIN files f ON f.id=ep.photo_id WHERE ep.event_id=events.id
                AND COALESCE(f.borrado,0)=0 AND f.capture_date IS NOT NULL), end_time)
            WHERE COALESCE(confirmado,0)=0""")
        c.commit()
        n = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        print(f"\nlisto. eventos en la base: {n:,}")
    else:
        print(f"\neventos que quedarian: {len(todos) - len(vacios) - (n_ev - len(grupos)):,}"
              f"  (hoy hay {len(todos):,})")
        print("dry-run: no se escribio nada. --aplicar para hacerlo.")
    c.close()


if __name__ == "__main__":
    main()
