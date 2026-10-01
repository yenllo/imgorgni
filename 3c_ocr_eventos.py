"""
Fase 3c: OCR selectivo para nombrar eventos con texto real.

Correr OCR sobre 150.000 fotos costaría ~21 h (513 ms c/u). En vez de eso se
toman unas pocas fotos representativas por evento: un cartel de "Cumpleaños de
Marta" aparece en varias fotos del mismo evento, así que con muestrear alcanza.
Con ~1.500 eventos x 5 fotos son ~40 minutos.

Corre DESPUÉS de la Fase 3 (clustering) y antes del export.

    python 3c_ocr_eventos.py
    python 3c_ocr_eventos.py --por-evento 8     # muestrear más
    python 3c_ocr_eventos.py --aplicar          # escribir el nombre encontrado
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

from db import get_conn
from utils_images import load_image_array
from utils_log import Spinner, log_step
import utils_ocr

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _asegurar_columnas(conn: sqlite3.Connection):
    for tabla, col, tipo in (("events", "ocr_texto", "TEXT"),
                             ("events", "ocr_pista", "TEXT")):
        existentes = {r[1] for r in conn.execute(f"PRAGMA table_info({tabla})")}
        if col not in existentes:
            conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}")
    conn.commit()


def elegir_muestra(conn, event_id: int, n: int):
    """
    Fotos del evento con más chance de tener texto útil.

    Prioriza las que el clasificador marcó con escenas de texto (carteles,
    documentos, pizarras); completa con un muestreo temporal para no mirar
    siempre el mismo momento del evento.
    """
    filas = conn.execute("""
        SELECT f.id, f.file_path, f.capture_date, pf.scene_tags, pf.quality_json
        FROM event_photos ep
        JOIN files f ON ep.photo_id = f.id
        LEFT JOIN photo_features pf ON f.id = pf.photo_id
        WHERE ep.event_id = ?
        ORDER BY f.capture_date
    """, (event_id,)).fetchall()
    if not filas:
        return []

    # Los nombres de evento están en CARTELES de fiestas, no en capturas de
    # pantalla ni documentos: esos se saltean porque solo aportan ruido.
    con_texto, resto = [], []
    for fid, fp, fecha, escenas, calidad in filas:
        etiquetas = (escenas or "").lower()
        if "screenshot" in etiquetas or "document" in etiquetas:
            continue
        celebracion = any(k in etiquetas for k in
                          ("birthday", "wedding", "graduation", "party",
                           "concert", "parade", "christmas", "monument"))
        try:
            nitidez = json.loads(calidad or "{}").get("sharpness", 0)
        except json.JSONDecodeError:
            nitidez = 0
        # Una foto borrosa no da OCR utilizable
        if nitidez < 40:
            continue
        (con_texto if celebracion else resto).append((fp, nitidez))

    con_texto.sort(key=lambda x: -x[1])
    muestra = [fp for fp, _ in con_texto[:n]]
    if len(muestra) < n and resto:
        # Repartir a lo largo del evento en vez de tomar las primeras
        paso = max(1, len(resto) // (n - len(muestra)))
        muestra += [fp for fp, _ in resto[::paso]][:n - len(muestra)]
    return muestra


def run(config_path: str = "config.json", por_evento: int = 5,
        aplicar: bool = False, min_fotos: int = 3):
    if not utils_ocr.disponible():
        log_step("⚠️ RapidOCR no instalado:  pip install rapidocr-onnxruntime")
        return 1

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    conn = get_conn(cfg["db_path"])
    _asegurar_columnas(conn)

    eventos = conn.execute("""
        SELECT e.id, COALESCE(e.name, e.suggested_name), COUNT(ep.photo_id)
        FROM events e JOIN event_photos ep ON e.id = ep.event_id
        WHERE e.name IS NULL          -- respeta los que nombraste a mano
        GROUP BY e.id HAVING COUNT(ep.photo_id) >= ?
        ORDER BY COUNT(ep.photo_id) DESC
    """, (min_fotos,)).fetchall()

    log_step("=" * 60)
    log_step(f"FASE 3c: OCR sobre {len(eventos)} eventos "
             f"({por_evento} fotos c/u = ~{len(eventos)*por_evento} lecturas)")
    log_step(f"Modo: {'APLICAR nombres' if aplicar else 'solo reporte'}")
    log_step("=" * 60)

    spinner = Spinner("Leyendo texto...")
    spinner.start()

    con_pista, con_texto, procesados = 0, 0, 0
    hallazgos = []

    for event_id, nombre, n_fotos in eventos:
        muestra = elegir_muestra(conn, event_id, por_evento)
        bloques_evento = []
        for fp in muestra:
            if not Path(fp).exists():
                continue
            try:
                img = load_image_array(fp)
            except Exception:
                continue
            bloques_evento.extend(utils_ocr.leer_texto(img))
            procesados += 1

        if not bloques_evento:
            continue
        con_texto += 1
        texto = utils_ocr.texto_util(bloques_evento)
        pista = utils_ocr.pista_de_evento(bloques_evento)

        conn.execute("UPDATE events SET ocr_texto=?, ocr_pista=? WHERE id=?",
                     (texto, pista, event_id))
        if pista:
            con_pista += 1
            hallazgos.append((event_id, nombre, pista, n_fotos))
            if aplicar:
                conn.execute("UPDATE events SET suggested_name=? WHERE id=?",
                             (pista, event_id))
        spinner.text = f"Leyendo texto... {procesados} fotos, {con_pista} nombres"

    conn.commit()
    spinner.stop(f"OCR completado sobre {procesados} fotos")

    log_step("=" * 60)
    log_step(f"📄 Eventos con texto detectado : {con_texto}")
    log_step(f"🏷️ Eventos con nombre real     : {con_pista}")
    if hallazgos:
        log_step("\nNombres encontrados:")
        for eid, viejo, pista, n in hallazgos[:25]:
            log_step(f"   [{n:>4} fotos] {str(viejo)[:26]:<28} -> {pista}")
    if not aplicar and con_pista:
        log_step("\nPara usarlos como nombre de evento:")
        log_step("   python 3c_ocr_eventos.py --aplicar")
    log_step("=" * 60)
    conn.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="OCR selectivo para nombrar eventos")
    ap.add_argument("-c", "--config", default="config.json")
    ap.add_argument("--por-evento", type=int, default=5)
    ap.add_argument("--min-fotos", type=int, default=3)
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()
    sys.exit(run(args.config, args.por_evento, args.aplicar, args.min_fotos))
