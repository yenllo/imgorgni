"""
Asignar colecciones a mano: lo que la IA no puede saber de los píxeles.

Un viaje a Italia es transversal a las categorías (paisajes + comida + gente),
y si es "viaje de estudio" o "trabajo fotográfico" eso solo lo sabés vos. Esta
herramienta te deja marcar eventos/viajes con un tipo y un nombre; el export
los agrupa cohesivos bajo su propia carpeta, sin repartirlos por escena.

    python etiquetar_colecciones.py --listar
    python etiquetar_colecciones.py --marcar 47 --tipo vde --nombre "Italia 2025"
    python etiquetar_colecciones.py --marcar 47,48,49 --tipo vde --nombre "Italia 2025"
    python etiquetar_colecciones.py --marcar-viaje "Rome, IT" --tipo vde --nombre "Italia 2025"
    python etiquetar_colecciones.py --quitar 47
"""
import argparse
import json
import sqlite3
import sys

from db import get_conn
from utils_log import log_step

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TIPOS = {
    "viaje": "Viajes",
    "vde": "Viajes de estudio",
    "trabajo": "Trabajos fotograficos",
    "evento": "Eventos",
    "proyecto": "Proyectos",
}


def _asegurar(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    for c in ("coleccion", "coleccion_tipo", "viaje"):
        if c not in cols:
            conn.execute(f"ALTER TABLE events ADD COLUMN {c} TEXT")
    conn.commit()


def listar(conn):
    filas = conn.execute("""
        SELECT e.id, COALESCE(e.name, e.suggested_name), e.start_time, e.viaje,
               e.coleccion, e.coleccion_tipo, COUNT(ep.photo_id)
        FROM events e LEFT JOIN event_photos ep ON e.id = ep.event_id
        GROUP BY e.id ORDER BY e.start_time
    """).fetchall()
    if not filas:
        log_step("No hay eventos. Corré la Fase 3 primero.")
        return
    log_step(f"{len(filas)} eventos:")
    log_step(f"{'ID':>5}  {'fecha':<11} {'fotos':>5}  {'viaje/colección':<26} nombre")
    for eid, nom, ini, viaje, col, ctipo, n in filas:
        marca = ""
        if col:
            marca = f"📌 {ctipo}:{col}"
        elif viaje:
            marca = f"🧳 {viaje}"
        log_step(f"{eid:>5}  {(ini or '')[:10]:<11} {n:>5}  {marca[:26]:<26} {str(nom)[:30]}")
    log_step("\nMarcá con:  python etiquetar_colecciones.py --marcar <ids> --tipo vde --nombre 'Italia 2025'")
    log_step("Tipos disponibles: " + ", ".join(TIPOS))


def marcar(conn, ids, tipo, nombre):
    if tipo not in TIPOS:
        log_step(f"⚠️ Tipo '{tipo}' desconocido. Usá: {', '.join(TIPOS)}")
        return
    conn.executemany(
        "UPDATE events SET coleccion=?, coleccion_tipo=? WHERE id=?",
        [(nombre, tipo, int(i)) for i in ids])
    conn.commit()
    log_step(f"✅ {len(ids)} eventos marcados como {TIPOS[tipo]} / '{nombre}'")


def marcar_viaje(conn, viaje_nombre, tipo, nombre):
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM events WHERE viaje LIKE ?", (f"%{viaje_nombre}%",))]
    if not ids:
        log_step(f"⚠️ Ningún evento con viaje '{viaje_nombre}'. Revisá con --listar")
        return
    marcar(conn, ids, tipo, nombre)


def quitar(conn, ids):
    conn.executemany(
        "UPDATE events SET coleccion=NULL, coleccion_tipo=NULL WHERE id=?",
        [(int(i),) for i in ids])
    conn.commit()
    log_step(f"✅ {len(ids)} eventos sin colección")


def main():
    ap = argparse.ArgumentParser(description="Asignar colecciones a mano")
    ap.add_argument("-c", "--config", default="config.json")
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--marcar", help="IDs de evento separados por coma")
    ap.add_argument("--marcar-viaje", help="Marca todos los eventos de un viaje detectado")
    ap.add_argument("--quitar", help="IDs a los que sacar la colección")
    ap.add_argument("--tipo", help=f"Tipo: {', '.join(TIPOS)}")
    ap.add_argument("--nombre", help="Nombre de la colección, ej: 'Italia 2025'")
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        conn = get_conn(json.load(f)["db_path"])
    _asegurar(conn)

    if args.marcar:
        if not (args.tipo and args.nombre):
            log_step("⚠️ Falta --tipo y --nombre"); return
        marcar(conn, args.marcar.split(","), args.tipo, args.nombre)
    elif args.marcar_viaje:
        if not (args.tipo and args.nombre):
            log_step("⚠️ Falta --tipo y --nombre"); return
        marcar_viaje(conn, args.marcar_viaje, args.tipo, args.nombre)
    elif args.quitar:
        quitar(conn, args.quitar.split(","))
    else:
        listar(conn)
    conn.close()


if __name__ == "__main__":
    main()
