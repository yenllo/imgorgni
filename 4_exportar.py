"""
Fase 4: Exporta a una jerarquía de carpetas con nombres descriptivos.

    Fotos eventos/
    └── 2024-01 Bariloche (5 dias)/              <- viaje (evento padre)
        ├── 2024-01-05 Bariloche - playa/
        └── 2024-01-06 Bariloche - montaña/
    Fotos personas/
    └── 2024-12-25 Navidad - Marta, Fer/
    Telefono/                                     <- todo lo de celular, aparte
    └── Fotos paisajes/...
    _Sueltas/2024-03/                             <- fotos aisladas, sin evento
    _No son fotos/                                <- iconos y assets web

Por defecto usa hardlinks: las carpetas no ocupan espacio extra.

    python 4_exportar.py --out E:/Fotos_Organizadas_IA
    python 4_exportar.py --copy --por-persona
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from db import get_conn
from utils_analisis import (categorize_event, clasificar_dispositivo, es_no_foto,
                            etiqueta_es, is_low_quality)
from utils_log import log_step

# Todo lo que no salió de una cámara dedicada va acá, sin organizar
CARPETA_TELEFONO = "_Telefono (sin organizar)"
CARPETA_NO_FOTOS = "_No son fotos"
CARPETA_SUELTAS = "_Sueltas"
CARPETA_VIAJES = "Viajes"

# Tipos de colección que el usuario puede asignar a mano. Cada uno es una
# carpeta de primer nivel: agrupa cosas que la IA no puede inferir de los píxeles.
# Las claves son los valores que el curador guarda de verdad en
# events.coleccion_tipo (templates/evento.html). Estaban desalineadas ("vde",
# "evento", "viaje") y esos eventos caian al generico "Colecciones" en vez de
# a su carpeta. Si agregas un tipo en el <select> del curador, agregalo aca.
TIPOS_COLECCION = {
    "viaje_estudio": "Viajes de estudio",
    "trabajo": "Trabajos fotograficos",
    "evento_fotografico": "Eventos",
    "familia": "Familia",
    "proyecto": "Proyectos",
}
MIN_FOTOS_EVENTO = 4          # por debajo, va a _Sueltas/YYYY-MM


def sanitize(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:max_len] or "Sin nombre"


def place_file(src: Path, dest: Path, mode: str) -> bool:
    if dest.exists():
        return False
    if mode == "move":
        shutil.move(str(src), str(dest))
    elif mode == "copy":
        shutil.copy2(str(src), str(dest))
    else:
        try:
            os.link(str(src), str(dest))
        except OSError:
            shutil.copy2(str(src), str(dest))
    return True


def _columna_existe(conn, tabla, col) -> bool:
    return col in {r[1] for r in conn.execute(f"PRAGMA table_info({tabla})")}


def _asegurar_columnas_coleccion(conn):
    """Columnas donde el usuario marca colecciones a mano (viaje de estudio, etc.)."""
    for col in ("coleccion", "coleccion_tipo"):
        if not _columna_existe(conn, "events", col):
            conn.execute(f"ALTER TABLE events ADD COLUMN {col} TEXT")
    conn.commit()


def nombre_evento(fecha: str, lugar, escenas, personas, n_fotos: int,
                  ocr_pista=None, nombre_manual=None) -> str:
    """
    Compone el nombre a partir de todo lo que sabemos.

    Prioridad: nombre puesto a mano > texto leído por OCR > lugar + escena.
    Las personas se agregan si son pocas y están nombradas.
    """
    dia = (fecha or "")[:10]

    if nombre_manual:
        return sanitize(f"{dia} {nombre_manual}")
    if ocr_pista:
        return sanitize(f"{dia} {ocr_pista}")

    partes = []
    if lugar:
        partes.append(lugar)
    if escenas:
        partes.append(etiqueta_es(escenas[0]))
    etiqueta = " - ".join(partes) if partes else "sin clasificar"

    if personas:
        gente = ", ".join(personas[:3])
        if len(personas) > 3:
            gente += f" +{len(personas)-3}"
        etiqueta = f"{etiqueta} - {gente}"

    return sanitize(f"{dia} {etiqueta}")


def run(config_path: str = "config.json", out_dir: str = None, mode: str = "link",
        por_persona: bool = False, min_fotos_persona: int = 15,
        min_fotos_evento: int = MIN_FOTOS_EVENTO):
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    out = Path(out_dir) if out_dir else Path(cfg["root_dir"]) / "_organizadas"
    out.mkdir(parents=True, exist_ok=True)
    conn = get_conn(cfg["db_path"])

    _asegurar_columnas_coleccion(conn)
    cols = ["e.id", "e.name", "e.suggested_name", "e.start_time"]
    cols.append("e.viaje" if _columna_existe(conn, "events", "viaje") else "NULL")
    cols.append("e.ocr_pista" if _columna_existe(conn, "events", "ocr_pista") else "NULL")
    cols.append("e.coleccion")       # nombre puesto a mano (ej: "Italia 2025")
    cols.append("e.coleccion_tipo")  # ej: "vde", "trabajo"

    eventos = conn.execute(
        f"SELECT {', '.join(cols)} FROM events e ORDER BY e.start_time").fetchall()

    # person_id -> nombre, para poder usarlos en los nombres de carpeta
    nombres_persona = {}
    try:
        nombres_persona = {pid: n for pid, n in conn.execute(
            "SELECT id, name FROM persons WHERE name IS NOT NULL AND name != ''")}
    except sqlite3.Error:
        pass

    log_step(f"Exportando {len(eventos)} eventos a {out}  (modo: {mode})")

    total_ok, total_missing, sueltas, telefono = 0, 0, 0, 0
    por_categoria = Counter()

    for (event_id, nombre_manual, sugerido, inicio, viaje, ocr_pista,
         coleccion, coleccion_tipo) in eventos:
        col_tipo = TIPOS_COLECCION.get((coleccion_tipo or "").lower(), "Colecciones") \
            if coleccion else None
        fotos = conn.execute("""
            SELECT f.id, f.file_path, f.place, pf.scene_tags, f.camera_model,
                   pf.quality_json
            FROM event_photos ep
            JOIN files f ON ep.photo_id = f.id
            LEFT JOIN photo_features pf ON f.id = pf.photo_id
            WHERE ep.event_id = ?
        """, (event_id,)).fetchall()
        if not fotos:
            continue

        escenas_l, lugares, calidades, dispositivos = [], [], [], []
        for _, fp_i, place, scene_json, cm_i, qj_i in fotos:
            def _carga(x, d):
                try:
                    return json.loads(x or d)
                except json.JSONDecodeError:
                    return json.loads(d)
            escenas_l.append(_carga(scene_json, "[]"))
            calidades.append(_carga(qj_i, "{}"))
            dispositivos.append(clasificar_dispositivo(cm_i, fp_i))
            if place:
                lugares.append(place)

        ids = [r[0] for r in fotos]
        personas_ev = []
        try:
            marcas = ",".join("?" * len(ids))
            for (pid,) in conn.execute(
                    f"SELECT DISTINCT person_id FROM faces WHERE photo_id IN ({marcas}) "
                    "AND person_id >= 0", ids):
                if pid in nombres_persona:
                    personas_ev.append(nombres_persona[pid])
        except sqlite3.Error:
            pass

        categoria = categorize_event(escenas_l, bool(personas_ev), calidades, dispositivos)
        lugar = Counter(lugares).most_common(1)[0][0] if lugares else None
        escenas_top = [s for s, _ in Counter(
            s for l in escenas_l for s in l).most_common(3)]

        carpeta_ev = nombre_evento(inicio, lugar, escenas_top, sorted(personas_ev),
                                   len(fotos), ocr_pista, nombre_manual)

        # Eventos muy chicos: no merecen carpeta propia
        es_suelta = len(fotos) < min_fotos_evento and not nombre_manual

        for _, fp, _, _, camera_model, quality_json in fotos:
            src = Path(fp)
            if not src.exists():
                total_missing += 1
                continue
            try:
                quality = json.loads(quality_json or "{}")
            except json.JSONDecodeError:
                quality = {}

            disp = clasificar_dispositivo(camera_model, fp)

            if es_no_foto(camera_model, fp, quality):
                destino, clave = out / CARPETA_NO_FOTOS, CARPETA_NO_FOTOS
            elif coleccion:
                # Colección marcada a mano (viaje de estudio, trabajo…): GANA sobre
                # todo, incluso sobre la exclusión de teléfono. Si el usuario dijo
                # "esto es mi viaje a Italia", va junto aunque sean fotos de celular.
                destino = out / col_tipo / sanitize(coleccion) / carpeta_ev
                clave = col_tipo
            elif disp != "camara":
                # No fue tomada por una cámara dedicada: va a UNA sola carpeta, sin
                # organizar. (Salvo que la hayas metido en una colección, arriba.)
                destino, clave = out / CARPETA_TELEFONO, CARPETA_TELEFONO
                telefono += 1
            elif viaje:
                # Viaje detectado por GPS+tiempo (solo fotos de cámara acá, las de
                # teléfono ya se descartaron): cohesivo, no se reparte por escena.
                destino = out / CARPETA_VIAJES / sanitize(viaje) / carpeta_ev
                clave = CARPETA_VIAJES
            elif es_suelta:
                destino = out / CARPETA_SUELTAS / (inicio or "")[:7]
                clave = CARPETA_SUELTAS
                sueltas += 1
            else:
                cat = "Fotos descartes" if is_low_quality(quality) else categoria
                destino = out / cat / carpeta_ev
                clave = cat

            destino.mkdir(parents=True, exist_ok=True)
            try:
                if place_file(src, destino / src.name, mode):
                    total_ok += 1
                    por_categoria[clave] += 1
            except Exception as e:
                log_step(f"⚠️ {src.name}: {e}")

    # Pasada final: fotos que no entraron a ningún evento (las de teléfono
    # salteadas en Fase 2, o cámara que quedó como ruido). Sin esto se perderían.
    # borrado=0: lo que mandaste al basurero desde el curador NO se exporta. Sin
    # este filtro se colaban 15.240 fotos ya descartadas (medido el 2026-08-16),
    # o sea que el export deshacia la limpieza a mano.
    huerfanas = conn.execute("""
        SELECT f.id, f.file_path, f.camera_model, pf.quality_json, f.capture_date
        FROM files f
        LEFT JOIN photo_features pf ON f.id = pf.photo_id
        WHERE f.id NOT IN (SELECT photo_id FROM event_photos)
          AND COALESCE(f.borrado, 0) = 0
    """).fetchall()
    for _, fp, camera_model, quality_json, capture_date in huerfanas:
        src = Path(fp)
        if not src.exists():
            total_missing += 1
            continue
        try:
            quality = json.loads(quality_json or "{}")
        except (json.JSONDecodeError, TypeError):
            quality = {}
        if es_no_foto(camera_model, fp, quality):
            destino, clave = out / CARPETA_NO_FOTOS, CARPETA_NO_FOTOS
        elif clasificar_dispositivo(camera_model, fp) != "camara":
            destino, clave = out / CARPETA_TELEFONO, CARPETA_TELEFONO
            telefono += 1
        else:
            # Por mes, igual que las sueltas que vienen de un evento chico. Antes
            # caian todas juntas en "sin_fecha" aunque tuvieran fecha: 4.152 fotos
            # de camara en una sola carpeta imposible de mirar.
            destino = out / CARPETA_SUELTAS / ((capture_date or "")[:7] or "sin_fecha")
            clave = CARPETA_SUELTAS
            sueltas += 1
        destino.mkdir(parents=True, exist_ok=True)
        try:
            if place_file(src, destino / src.name, mode):
                total_ok += 1
                por_categoria[clave] += 1
        except Exception:
            pass

    if por_persona:
        _exportar_personas(conn, out, mode, min_fotos_persona, por_categoria,
                           nombres_persona)
    conn.close()

    log_step("=" * 60)
    log_step(f"✅ {total_ok:,} fotos exportadas"
             + (f"  ({total_missing:,} no encontradas)" if total_missing else ""))
    for cat, n in por_categoria.most_common():
        log_step(f"   {cat:<34} {n:>6,}")
    if telefono:
        log_step(f"\n   📱 {telefono:,} fotos NO-cámara a {CARPETA_TELEFONO}/ (sin organizar)")
    if sueltas:
        log_step(f"   {sueltas:,} fotos de cámara aisladas fueron a {CARPETA_SUELTAS}/")
    log_step("=" * 60)


def _exportar_personas(conn, out: Path, mode: str, min_fotos: int,
                       contador: Counter, nombres: dict):
    try:
        filas = conn.execute("""
            SELECT fa.person_id, f.file_path FROM faces fa
            JOIN files f ON fa.photo_id = f.id
            WHERE fa.person_id IS NOT NULL AND fa.person_id >= 0
        """).fetchall()
    except sqlite3.Error:
        return
    if not filas:
        return

    por_persona = defaultdict(set)
    for person_id, fp in filas:
        por_persona[person_id].add(fp)

    log_step(f"Exportando {len(por_persona)} personas detectadas...")
    for person_id, paths in por_persona.items():
        if len(paths) < min_fotos:
            continue
        etiqueta = nombres.get(person_id) or f"Persona {person_id}"
        carpeta = out / "Fotos personas" / sanitize(etiqueta)
        carpeta.mkdir(parents=True, exist_ok=True)
        for fp in paths:
            src = Path(fp)
            if not src.exists():
                continue
            try:
                if place_file(src, carpeta / src.name, mode):
                    contador["Fotos personas (por rostro)"] += 1
            except Exception:
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Exportar eventos a carpetas")
    parser.add_argument("-c", "--config", default="config.json")
    parser.add_argument("--out", default=None)
    parser.add_argument("--por-persona", action="store_true")
    parser.add_argument("--min-fotos-persona", type=int, default=15)
    parser.add_argument("--min-fotos-evento", type=int, default=MIN_FOTOS_EVENTO)
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument("--copy", action="store_true")
    grupo.add_argument("--move", action="store_true")
    args = parser.parse_args()

    run(config_path=args.config, out_dir=args.out,
        mode="move" if args.move else ("copy" if args.copy else "link"),
        por_persona=args.por_persona, min_fotos_persona=args.min_fotos_persona,
        min_fotos_evento=args.min_fotos_evento)
