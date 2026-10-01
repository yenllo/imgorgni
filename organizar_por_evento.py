#!/usr/bin/env python3
"""Mueve las fotos de los eventos CONFIRMADOS a una carpeta por evento.

    /mnt/fotos/galeria/
    ├── LigaExScuola/
    │   └── 2026-07-11 F9 - 11-07-2026 (Final)/
    │       ├── evento.json
    │       └── DSC00184.jpg ...
    └── 2023-05-24 Partido de futbol SIVM/
        ├── evento.json
        └── ...

Asi "carpeta = evento" pasa a ser cierto en el disco y no solo en la base: si
algun dia se pierde photos_v2.db, el archivo sigue navegable y cada carpeta se
explica sola. El `evento.json` guarda la identidad (id, colección, etiquetas)
que el nombre de la carpeta deliberadamente no lleva.

El nombre sale de **la fecha mas el nombre que pusiste vos**. No se usa el OCR
ni el nombre sugerido por la IA: esto corre solo sobre eventos confirmados, que
son justamente los que ya revisaste y nombraste a mano.

SEGURIDAD
  - Por defecto SIMULA. No mueve nada sin --mover.
  - Origen y destino tienen que estar en el mismo sistema de archivos. Si no,
    aborta: mover entre discos es copiar y puede quedar a medias.
  - Cada movimiento se anota en una bitacora ANTES de tocar la base, asi que
    todo se puede revertir con --deshacer o completar con --reparar.
  - Una foto en dos eventos confirmados va a la del evento de id mas bajo. La
    base sigue sabiendo que pertenece a los dos.

    python organizar_por_evento.py                 # simulacro, no toca nada
    python organizar_por_evento.py --mover
    python organizar_por_evento.py --deshacer bitacora.jsonl
    python organizar_por_evento.py --reparar bitacora.jsonl   # tras un corte
"""
import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

DB = os.environ.get("CURADOR_DB", "/home/yenllo/curador/photos_v2.db")
DESTINO = Path(os.environ.get("GALERIA_EVENTOS", "/mnt/fotos/galeria"))
LOTE = 200


def sanitize(name, max_len=80):
    """Igual que la Fase 4 (4_exportar.py): una sola forma de limpiar nombres."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "").strip(" .")
    return name[:max_len] or "Sin nombre"


# Fechas escritas dentro del nombre a mano: `11-07-2026`, `2026-07-11`.
_FECHA_EN_NOMBRE = re.compile(r"\b(\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{4}[-/]\d{1,2}[-/]\d{1,2})\b")
_MES = re.compile(r"\b(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)", re.I)


def _sin_fecha_repetida(nom):
    """`F9 - 11-07-2026 (Final)` -> `F9 (Final)`.

    El prefijo ISO va adelante igual, asi que dejar la fecha tambien adentro la
    escribe dos veces. Se saca la del nombre y se limpian los separadores que
    quedan colgando.
    """
    limpio = _FECHA_EN_NOMBRE.sub("", nom)
    limpio = re.sub(r"\s*-\s*-\s*", " - ", limpio)      # ` - - ` que quedo al medio
    limpio = re.sub(r"\s*-\s*(?=\()", " ", limpio)      # `F9 - (Final)` -> `F9 (Final)`
    limpio = re.sub(r"\(\s*\)", "", limpio)             # parentesis que quedo vacio
    limpio = re.sub(r"\s{2,}", " ", limpio)
    return limpio.strip(" -,") or nom.strip()


def nombre_carpeta(ev):
    """`2023-05-24 Partido de futbol SIVM`.

    Prefijo ISO siempre, para que el Explorador las ordene cronologicamente, y
    la fecha se saca del nombre si vos ya la habias escrito ahi.

    Solo el nombre manual: estos eventos ya los revisaste. El suggested_name
    queda como red por si algun confirmado quedo sin nombre.
    """
    ini = (ev["start_time"] or "")[:10]
    fin = (ev["end_time"] or "")[:10]
    crudo = (ev["name"] or "").strip() or (ev["suggested_name"] or "").strip()
    nom = _sin_fecha_repetida(crudo)
    etiqueta = f"{ini} {nom}".strip()

    # `(N dias)` solo si el nombre no habla ya de fechas: en
    # `Settimana 2024 (28 jun, 1, 2 y 3 de julio)` sobra.
    if ini and fin and fin != ini and not _MES.search(crudo):
        try:
            d = (dt.date.fromisoformat(fin) - dt.date.fromisoformat(ini)).days + 1
            if d > 1:
                etiqueta += f" ({d} dias)"
        except ValueError:
            pass
    return sanitize(etiqueta)


# La misma lista que usa el curador para su filtro de formato.
RAW_EXT = (".arw", ".cr2", ".cr3", ".dng", ".nef", ".orf", ".raf", ".rw2")


def destino_de(ev):
    """Con coleccion queda un nivel intermedio; sin ella, cuelga de la raiz."""
    col = (ev["coleccion"] or "").strip()
    base = DESTINO / sanitize(col) if col else DESTINO
    return base / nombre_carpeta(ev)


def carpeta_de_foto(ev, ruta):
    """Los RAW van a un subdirectorio `RAW/` dentro del evento.

    No es cosmetico: la carpeta del evento es lo que le pasas a alguien, y un
    .ARW no lo puede abrir —y en un negativo escaneado ademas esta invertido—.
    Separandolos, compartir la carpeta del evento entrega solo lo procesado.

    Sigue siendo UN evento en la base: el curador ya distingue RAW de JPEG con
    su filtro de formato, sin necesidad de partirlo en dos.
    """
    ev_dir = destino_de(ev)
    return ev_dir / "RAW" if Path(ruta).suffix.lower() in RAW_EXT else ev_dir


def eventos_confirmados(cx):
    return {r["id"]: r for r in cx.execute(
        "SELECT id, name, suggested_name, start_time, end_time, coleccion, "
        "       coleccion_tipo, notas "
        "  FROM events WHERE COALESCE(confirmado,0)=1")}


def plan(cx, evs):
    """[(photo_id, ruta_actual, evento_id)].

    La subconsulta resuelve las fotos que estan en varios eventos confirmados:
    una foto no puede vivir en dos carpetas, asi que se queda con el evento de
    id mas bajo (el primero que se creo).
    """
    filas = cx.execute("""
        SELECT p.photo_id, f.file_path, p.eid
          FROM (SELECT ep.photo_id, MIN(ep.event_id) eid
                  FROM event_photos ep
                  JOIN events e ON e.id = ep.event_id
                 WHERE COALESCE(e.confirmado,0)=1
                 GROUP BY ep.photo_id) p
          JOIN files f ON f.id = p.photo_id
         WHERE COALESCE(f.borrado,0)=0
    """).fetchall()
    return [(r["photo_id"], r["file_path"], r["eid"]) for r in filas
            if r["eid"] in evs]


def ficha(cx, ev, cuantas):
    """Lo que el nombre de la carpeta no puede llevar: identidad y clasificacion."""
    try:
        temas = [{"etiqueta": r[0], "fotos": r[1]} for r in cx.execute(
            "SELECT tf.tag, COUNT(*) n FROM tag_foto tf "
            "JOIN event_photos ep ON ep.photo_id = tf.photo_id "
            "WHERE ep.event_id = ? GROUP BY tf.tag ORDER BY n DESC LIMIT 8",
            (ev["id"],))]
    except sqlite3.OperationalError:
        temas = []          # sin tag_foto todavia: la ficha sale igual
    return {
        "id": ev["id"],
        "nombre": ev["name"] or ev["suggested_name"],
        "inicio": ev["start_time"],
        "fin": ev["end_time"],
        "coleccion": ev["coleccion"],
        "coleccion_tipo": ev["coleccion_tipo"],
        "notas": ev["notas"],
        "fotos": cuantas,
        "etiquetas": temas,
        "generado": dt.datetime.now().isoformat(timespec="seconds"),
        "origen": "organizar_por_evento.py sobre photos_v2.db",
    }


def mismo_disco(a, b):
    """Un rename dentro del mismo sistema de archivos es instantaneo. Entre
    discos es copiar y borrar, que puede quedar a medias: no lo hacemos."""
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def mover(cx, hacerlo, bitacora):
    evs = eventos_confirmados(cx)
    tareas = plan(cx, evs)
    print(f"  {len(evs)} eventos confirmados, {len(tareas):,} fotos")
    if not tareas:
        return

    DESTINO.mkdir(parents=True, exist_ok=True)
    if not mismo_disco(DESTINO, os.path.dirname(tareas[0][1])):
        sys.exit(f"ABORTA: {DESTINO} no esta en el mismo disco que las fotos.\n"
                 "  Mover entre discos es copiar, y puede quedar a medias.")

    movidas = faltan = colisiones = crudas = 0
    por_evento = {}
    log = open(bitacora, "a", encoding="utf-8") if hacerlo else None
    t0 = time.perf_counter()
    pendientes = []

    for pid, viejo, eid in tareas:
        if not os.path.exists(viejo):
            faltan += 1
            continue
        dst_dir = carpeta_de_foto(evs[eid], viejo)
        nuevo = dst_dir / os.path.basename(viejo)
        if str(nuevo) == viejo:
            por_evento[eid] = por_evento.get(eid, 0) + 1
            continue                       # ya esta en su lugar
        n = 2
        while nuevo.exists():              # dos carpetas de origen, mismo nombre
            colisiones += 1
            nuevo = dst_dir / f"{Path(viejo).stem}-{n}{Path(viejo).suffix}"
            n += 1

        movidas += 1
        crudas += dst_dir.name == "RAW"
        por_evento[eid] = por_evento.get(eid, 0) + 1
        if not hacerlo:
            if movidas <= 4:
                print(f"    {viejo}\n      -> {nuevo}")
            continue

        dst_dir.mkdir(parents=True, exist_ok=True)
        # la bitacora se escribe ANTES de tocar la base: si el proceso se corta
        # entre el rename y el UPDATE, --reparar termina el trabajo
        log.write(json.dumps({"id": pid, "de": viejo, "a": str(nuevo)}) + "\n")
        log.flush()
        os.rename(viejo, nuevo)
        pendientes.append((str(nuevo), pid))
        if len(pendientes) >= LOTE:
            cx.executemany("UPDATE files SET file_path=? WHERE id=?", pendientes)
            cx.commit()
            pendientes.clear()
            print(f"\r    {movidas:,}/{len(tareas):,}", end="", flush=True)

    if pendientes:
        cx.executemany("UPDATE files SET file_path=? WHERE id=?", pendientes)
        cx.commit()

    if hacerlo:
        for eid, cuantas in por_evento.items():
            d = destino_de(evs[eid])
            d.mkdir(parents=True, exist_ok=True)
            (d / "evento.json").write_text(
                json.dumps(ficha(cx, evs[eid], cuantas), ensure_ascii=False, indent=2),
                encoding="utf-8")
        log.close()

    print(f"\n  {'moveria' if not hacerlo else 'movio'} {movidas:,} fotos "
          f"a {len(por_evento)} carpetas en {time.perf_counter() - t0:.0f}s")
    print(f"  de esas, {crudas:,} son RAW y van a la subcarpeta RAW/ del evento")
    if faltan:
        print(f"  {faltan:,} no estaban en el disco (se saltearon)")
    if colisiones:
        print(f"  {colisiones:,} tenian nombre repetido y se renombraron")
    if not hacerlo:
        print("\n  ejemplos de carpeta:")
        for eid in list(por_evento)[:6]:
            print(f"    {destino_de(evs[eid])}")
    else:
        print(f"  bitacora: {bitacora}  (revertir: --deshacer {bitacora})")


def deshacer(cx, bitacora):
    lineas = [json.loads(l) for l in open(bitacora, encoding="utf-8") if l.strip()]
    print(f"  {len(lineas):,} movimientos para revertir")
    vuelto = 0
    for r in reversed(lineas):
        if os.path.exists(r["a"]) and not os.path.exists(r["de"]):
            os.makedirs(os.path.dirname(r["de"]), exist_ok=True)
            os.rename(r["a"], r["de"])
            cx.execute("UPDATE files SET file_path=? WHERE id=?", (r["de"], r["id"]))
            vuelto += 1
    cx.commit()
    print(f"  volvieron {vuelto:,} fotos a su lugar original")


def reparar(cx, bitacora):
    """Tras un corte: pone en la base las rutas de lo que ya se movio en disco."""
    lineas = [json.loads(l) for l in open(bitacora, encoding="utf-8") if l.strip()]
    n = 0
    for r in lineas:
        if os.path.exists(r["a"]):
            n += cx.execute("UPDATE files SET file_path=? WHERE id=? AND file_path<>?",
                            (r["a"], r["id"], r["a"])).rowcount
    cx.commit()
    print(f"  {n:,} rutas corregidas en la base")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mover", action="store_true", help="hacerlo de verdad")
    ap.add_argument("--deshacer", metavar="BITACORA")
    ap.add_argument("--reparar", metavar="BITACORA")
    ap.add_argument("--db", default=DB)
    a = ap.parse_args()

    cx = sqlite3.connect(a.db, timeout=60)
    cx.row_factory = sqlite3.Row
    if a.deshacer:
        deshacer(cx, a.deshacer)
    elif a.reparar:
        reparar(cx, a.reparar)
    else:
        if not a.mover:
            print("  SIMULACRO — no se toca nada. Agrega --mover para hacerlo.\n")
        mover(cx, a.mover, f"organizar-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    cx.close()


if __name__ == "__main__":
    main()
