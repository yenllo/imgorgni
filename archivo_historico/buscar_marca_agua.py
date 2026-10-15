"""Corre OCR sobre fotos de eventos/trabajos (no la biblioteca personal
completa) para detectar marcas de agua de texto, sin depender de un diseño
fijo como hace detect_watermark() (que solo busca UNA plantilla). La mayoria
de estas fotos no tienen nada de texto real superpuesto, asi que "OCR
encontro texto" ya es una señal razonable de marca de agua.

Resumible: cada foto procesada (tenga o no texto) se guarda en
ocr_marca_agua, asi que retomar el script no reprocesa nada.

    python buscar_marca_agua.py
"""
import sqlite3
import time

import pytesseract
from PIL import Image

# Sube el tope de PIL a 300 Mpx: sin esto las fotos de 199,8 Mpx del modo alta
# resolucion del telefono ni siquiera se llegan a mirar por OCR.
from utils_images import MAX_PIXELES_FOTO

Image.MAX_IMAGE_PIXELS = MAX_PIXELES_FOTO

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

DB = "C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
CARPETAS = ["%LigaExScuola%", "%Fotos eventos%", "%Fotos concientemente%",
            "%taller de fotografia%"]
UMBRAL_CARACTERES = 8  # mas que esto de texto real detectado = probable marca de agua


def main():
    conn = sqlite3.connect(DB, timeout=180)
    conn.execute("""CREATE TABLE IF NOT EXISTS ocr_marca_agua (
        photo_id INTEGER PRIMARY KEY, texto TEXT, tiene_marca INTEGER)""")
    conn.commit()

    condiciones = " OR ".join("file_path LIKE ?" for _ in CARPETAS)
    pend = conn.execute(f"""
        SELECT id, file_path FROM files
        WHERE COALESCE(borrado,0)=0 AND ({condiciones})
          AND (file_path LIKE '%.jpg' OR file_path LIKE '%.JPG' OR file_path LIKE '%.jpeg' OR file_path LIKE '%.JPEG')
          AND id NOT IN (SELECT photo_id FROM ocr_marca_agua)
    """, CARPETAS).fetchall()
    print(f"pendientes: {len(pend):,}", flush=True)
    if not pend:
        return

    t0 = time.time()
    procesadas = con_marca = 0
    lote = []
    for i, (fid, fp) in enumerate(pend, 1):
        texto = ""
        try:
            im = Image.open(fp).convert("L")
            im.thumbnail((1200, 1200))
            texto = pytesseract.image_to_string(im, timeout=10).strip()
        except Exception:
            pass
        tiene = 1 if len(texto) >= UMBRAL_CARACTERES else 0
        con_marca += tiene
        lote.append((fid, texto, tiene))
        procesadas += 1
        if len(lote) >= 200:
            conn.executemany(
                "INSERT OR IGNORE INTO ocr_marca_agua (photo_id, texto, tiene_marca) VALUES (?,?,?)",
                lote)
            conn.commit()
            lote = []
            elapsed = time.time() - t0
            ritmo = procesadas / elapsed
            restantes = (len(pend) - procesadas) / ritmo if ritmo else 0
            print(f"  {procesadas:,}/{len(pend):,}  ({con_marca:,} con posible marca)  "
                  f"ETA {restantes/60:.0f} min", flush=True)
    if lote:
        conn.executemany(
            "INSERT OR IGNORE INTO ocr_marca_agua (photo_id, texto, tiene_marca) VALUES (?,?,?)",
            lote)
        conn.commit()

    print(f"listo: {procesadas:,} procesadas, {con_marca:,} con posible marca de agua")


if __name__ == "__main__":
    main()
