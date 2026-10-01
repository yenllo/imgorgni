"""
Utilidades de imagen: detección de tipo, hashing, EXIF, watermark, descarte y carga.
Reconstruido a partir de los usos en 1_indexado.py, 2_analisis.py y utils_batch.py.
"""
import hashlib
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

SUPPORTED_RAW_EXTS = {".cr2", ".arw", ".nef", ".rw2", ".raf", ".raw", ".dng"}
FAST_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# PIL se niega a abrir imagenes de mas de 178,9 Mpx: es una defensa contra
# "bombas de descompresion" pensada para servidores que reciben archivos de
# desconocidos. Aca las fotos son propias, y el modo alta resolucion del
# telefono da 16320x12240 = 199,8 Mpx: 114 fotos reales quedaban como "no se
# pudo leer" en el curador, y reclasificar_danados.py estaba a un paso de
# marcarlas danado=1. Mismo tope que MAX_MPX en web_curador.
MAX_PIXELES_FOTO = 300 * 10 ** 6


def _subir_tope_pil():
    from PIL import Image
    if (Image.MAX_IMAGE_PIXELS or 0) < MAX_PIXELES_FOTO:
        Image.MAX_IMAGE_PIXELS = MAX_PIXELES_FOTO


_subir_tope_pil()

_ORIENTACION_EXIF = 274                       # tag EXIF "Orientation"
_FLIP_A_ORIENTACION = {3: 3, 5: 8, 6: 6}      # rawpy.sizes.flip -> orientacion EXIF


def orientacion(im, flip: int = 0) -> int:
    """Como esta girada la imagen (1 = derecha, sin girar).

    La camara guarda la foto siempre igual y anota "esta girada" en el EXIF;
    enderezarla es trabajo del visor. PIL no lo hace solo, asi que las
    verticales salian acostadas: el 20% de los JPEG y el 35% de los RAW de
    esta biblioteca traen orientacion != 1.

    En los RAW la rotacion viene ademas en rawpy.sizes.flip, que coincide
    siempre con el EXIF del preview embebido (medido sobre 194 archivos:
    flip 0->1, 5->8, 6->6). Se prefiere el EXIF; *flip* es el respaldo para
    el preview bitmap, que no trae EXIF.
    """
    try:
        o = im.getexif().get(_ORIENTACION_EXIF, 1) or 1
    except Exception:
        o = 1
    return _FLIP_A_ORIENTACION.get(flip, 1) if o == 1 else o


def enderezar(im, flip: int = 0, orient: Optional[int] = None):
    """Imagen rotada como corresponde. Mismo mapeo que ImageOps.exif_transpose.

    *orient* sirve para enderezar un recorte: crop() no hereda el EXIF del
    original, asi que hay que leer la orientacion antes de recortar y pasarla
    aca a mano.
    """
    from PIL import Image
    o = orientacion(im, flip) if orient is None else orient
    giros = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
             4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
             6: Image.Transpose.ROTATE_270,      7: Image.Transpose.TRANSVERSE,
             8: Image.Transpose.ROTATE_90}
    if o not in giros:
        return im
    try:
        girada = im.transpose(giros[o])
    except Exception:
        return im   # una miniatura torcida es mejor que ninguna
    # transpose() se lleva el EXIF puesto, con la marca de girada incluida. Se
    # saca para que una segunda pasada no la vuelva a girar (crop() y convert()
    # tambien heredan info, asi que es facil que pase). En la miniatura no hace
    # falta el EXIF: PIL no lo escribe al guardar salvo que se le pase exif=.
    girada.info.pop("exif", None)
    return girada
TIFF_EXTS = {".tif", ".tiff"}
ALL_IMAGE_EXTS = FAST_EXTS | TIFF_EXTS | SUPPORTED_RAW_EXTS

# Patrones de nombre típicos de móvil/screenshot
_MOBILE_NAME_RE = re.compile(
    r"(screenshot|captura|screen[_ ]?shot|wa\d{4}|-wa\d|whatsapp|received_)", re.IGNORECASE)

# Resoluciones exactas de pantallas comunes (screenshots)
_SCREEN_SIZES = {
    (720, 1280), (1080, 1920), (1080, 2340), (1080, 2400), (1170, 2532),
    (1179, 2556), (1284, 2778), (1440, 2960), (1440, 3200), (1290, 2796),
}


def is_image_file(fp: str) -> bool:
    return Path(fp).suffix.lower() in ALL_IMAGE_EXTS


# Por encima de este tamaño no se hashea el archivo entero, sino tamaño +
# extremos. Un RAW de 40 MB pasa de leer 40 MB a leer 8 MB (5x más rápido en
# HDD). Dos archivos distintos con mismo tamaño Y mismos 4 MB iniciales Y
# finales es imposible en la práctica para fotos. Los chicos (JPG) se hashean
# completos como siempre.
HASH_COMPLETO_HASTA = 24 * 1024 * 1024   # 24 MB
BORDE = 4 * 1024 * 1024                    # 4 MB de cada extremo


def compute_sha256(fp: str, chunk_size: int = 1024 * 1024) -> str:
    size = 0
    try:
        size = os.path.getsize(fp)
    except OSError:
        pass

    h = hashlib.sha256()
    if size > HASH_COMPLETO_HASTA:
        # Hash parcial: tamaño + primeros y últimos BORDE bytes
        h.update(str(size).encode())
        with open(fp, "rb") as f:
            h.update(f.read(BORDE))
            f.seek(-BORDE, 2)
            h.update(f.read(BORDE))
        return "p" + h.hexdigest()   # prefijo 'p' distingue parcial de completo
    with open(fp, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _exif_date_to_iso(raw: str) -> Optional[str]:
    """'2023:05:12 14:30:22' -> '2023-05-12T14:30:22'"""
    raw = str(raw).strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).isoformat()
        except ValueError:
            continue
    return None


def find_takeout_sidecar(fp: str) -> Optional[Path]:
    """
    Busca el .json que Google Takeout deja al lado de cada foto.

    Takeout exporta las imágenes SIN EXIF embebido y pone los metadatos reales
    en un sidecar. Los nombres son inconsistentes entre versiones de Takeout,
    así que se prueban las variantes conocidas.
    """
    p = Path(fp)
    candidatos = [
        p.with_suffix(p.suffix + ".json"),                            # IMG_1.jpg.json
        p.with_suffix(p.suffix + ".supplemental-metadata.json"),      # formato 2024+
        p.with_suffix(".json"),                                       # IMG_1.json
    ]
    # Takeout trunca los nombres a 46 caracteres
    if len(p.name) > 46:
        candidatos.append(p.parent / (p.name[:46] + ".json"))
    for c in candidatos:
        if c.exists():
            return c
    return None


def read_takeout_sidecar(fp: str) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """Devuelve (capture_date_iso, lat, lon) desde el JSON de Takeout."""
    sidecar = find_takeout_sidecar(fp)
    if sidecar is None:
        return None, None, None
    try:
        import json
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception:
        return None, None, None

    capture_date = None
    for key in ("photoTakenTime", "creationTime"):
        ts = (data.get(key) or {}).get("timestamp")
        if ts:
            try:
                capture_date = datetime.fromtimestamp(int(ts)).isoformat()
                break
            except (ValueError, OSError):
                pass

    lat = lon = None
    for key in ("geoData", "geoDataExif"):
        geo = data.get(key) or {}
        if geo.get("latitude") or geo.get("longitude"):
            # Takeout escribe 0.0 cuando no hay dato real
            if geo["latitude"] or geo["longitude"]:
                lat, lon = float(geo["latitude"]), float(geo["longitude"])
                break

    return capture_date, lat, lon


def read_exif_info(fp: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Devuelve (capture_date_iso, camera_model).

    Orden de preferencia para la fecha:
      1. EXIF embebido (lo más confiable)
      2. Sidecar .json de Google Takeout (fotos bajadas de Google Photos)
      3. mtime del archivo (último recurso)
    """
    capture_date = None
    camera_model = None
    try:
        import exifread
        with open(fp, "rb") as f:
            tags = exifread.process_file(f, details=False, stop_tag="EXIF DateTimeOriginal")
        for key in ("EXIF DateTimeOriginal", "EXIF DateTimeDigitized", "Image DateTime"):
            if key in tags:
                capture_date = _exif_date_to_iso(tags[key])
                if capture_date:
                    break
        if "Image Model" in tags:
            camera_model = str(tags["Image Model"]).strip() or None
        # Anteponer la marca: modelos como "SM-G991B" o "Pixel 7" no se
        # identifican sin ella. Se omite si el modelo ya la contiene.
        make = str(tags.get("Image Make", "")).strip()
        if make and camera_model and make.split()[0].lower() not in camera_model.lower():
            camera_model = f"{make} {camera_model}"
        elif make and not camera_model:
            camera_model = make
    except Exception:
        pass

    if capture_date is None:
        capture_date, _, _ = read_takeout_sidecar(fp)

    if capture_date is None:
        try:
            capture_date = datetime.fromtimestamp(Path(fp).stat().st_mtime).isoformat()
        except Exception:
            pass

    return capture_date, camera_model


def detect_watermark(fp: str, template_gray: np.ndarray, threshold: float = 0.85) -> bool:
    """Busca la plantilla en el tercio inferior de la imagen con matchTemplate."""
    img = cv2.imread(fp, cv2.IMREAD_GRAYSCALE)
    if img is None or template_gray is None:
        return False
    th, tw = template_gray.shape[:2]
    ih, iw = img.shape[:2]
    if th > ih or tw > iw:
        return False
    # ponytail: solo tercio inferior, ahí van las marcas de agua; escaneo completo si hiciera falta
    region = img[ih * 2 // 3:, :]
    if region.shape[0] < th:
        region = img
    res = cv2.matchTemplate(region, template_gray, cv2.TM_CCOEFF_NORMED)
    return float(res.max()) >= float(threshold)


def move_to_discard(root_dir: str, fp: str, discard_dir: str):
    """Mueve fp a root_dir/discard_dir/ preservando la ruta relativa."""
    src = Path(fp)
    root = Path(root_dir)
    try:
        rel = src.relative_to(root)
    except ValueError:
        rel = Path(src.name)
    dest = root / discard_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():  # colisión: sufijo con hash corto del path
        dest = dest.with_name(f"{dest.stem}_{abs(hash(fp)) % 10000}{dest.suffix}")
    shutil.move(str(src), str(dest))


def mobile_or_screenshot(fp: str, camera_model: Optional[str]) -> bool:
    """Heurística: nombre típico de screenshot/WhatsApp, o PNG sin cámara con resolución de pantalla."""
    name = Path(fp).name
    if _MOBILE_NAME_RE.search(name):
        return True
    if camera_model:  # tiene EXIF de cámara real -> no es screenshot
        return False
    if Path(fp).suffix.lower() == ".png":
        try:
            img = cv2.imread(fp)
            if img is not None:
                h, w = img.shape[:2]
                if (w, h) in _SCREEN_SIZES or (h, w) in _SCREEN_SIZES:
                    return True
        except Exception:
            pass
    return False


# Los RAW traen un JPEG incrustado que generó la cámara. Extraerlo es ~6.5x más
# rápido que revelar el RAW (66 ms vs 436 ms medido sobre fotos reales) y su
# resolución sobra: CLIP usa 224px, YOLO 640px. Además refleja el procesado de
# la cámara, que se parece más a lo que ve una persona.
USAR_PREVIEW_RAW = True
MIN_LADO_PREVIEW = 640   # por debajo de esto, revelar el RAW completo


def _raw_preview(fp: str) -> Optional[np.ndarray]:
    """JPEG incrustado del RAW como BGR, o None si no sirve."""
    try:
        import rawpy
        with rawpy.imread(fp) as raw:
            thumb = raw.extract_thumb()
        if thumb.format != rawpy.ThumbFormat.JPEG:
            return None
        img = cv2.imdecode(np.frombuffer(thumb.data, np.uint8), cv2.IMREAD_COLOR)
        if img is None or min(img.shape[:2]) < MIN_LADO_PREVIEW:
            return None
        return img
    except Exception:
        return None


def load_image_array(fp: str) -> np.ndarray:
    """Carga imagen como np.ndarray BGR. RAW vía rawpy. Lanza excepción si falla."""
    ext = Path(fp).suffix.lower()
    if ext in SUPPORTED_RAW_EXTS:
        if USAR_PREVIEW_RAW:
            preview = _raw_preview(fp)
            if preview is not None:
                return preview
        import rawpy
        with rawpy.imread(fp) as raw:
            rgb = raw.postprocess(use_camera_wb=True, half_size=True, no_auto_bright=False)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    # np.fromfile + imdecode para soportar rutas con caracteres no-ASCII en Windows
    data = np.fromfile(fp, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"No se pudo decodificar: {fp}")
    return img


def recortar_cara(fp: str, bbox, margen: float = 0.3, tam_max: int = 200):
    """Recorta una cara de *fp* dado su bbox (mismo espacio de coordenadas que
    detecto FaceAnalyzer: preview embebido para RAW, imagen completa si no).
    Devuelve PIL.Image en RGB, o None si no se pudo. Compartido entre el
    crop-on-demand de /face/<id> y la seleccion de cara representativa."""
    from PIL import Image
    import io as _io
    x1, y1, x2, y2 = bbox
    ext = Path(fp).suffix.lower()
    flip = 0
    try:
        if ext in SUPPORTED_RAW_EXTS:
            import rawpy
            with rawpy.imread(fp) as raw:
                flip = raw.sizes.flip
                thumb = raw.extract_thumb()
            im = (Image.open(_io.BytesIO(thumb.data)) if thumb.format == rawpy.ThumbFormat.JPEG
                  else Image.fromarray(thumb.data))
            # El bbox vive en el espacio de load_image_array, que descarta el
            # preview incrustado cuando su lado corto no llega a
            # MIN_LADO_PREVIEW y revela el RAW entero (mas grande). Sin repetir
            # ese mismo corte aca, el recorte se hacia sobre el preview chico
            # con coordenadas de la imagen grande: crop() recibia una caja
            # invertida y /face/<id> devolvia el cuadro gris "Error rec".
            if min(im.size) < MIN_LADO_PREVIEW:
                im = Image.fromarray(
                    cv2.cvtColor(load_image_array(fp), cv2.COLOR_BGR2RGB))
                flip = 0   # postprocess ya entrega la imagen enderezada
        else:
            im = Image.open(fp)
        w, h = x2 - x1, y2 - y1
        mx, my = w * margen, h * margen
        nx1, ny1 = max(0, x1 - mx), max(0, y1 - my)
        nx2, ny2 = min(im.width, x2 + mx), min(im.height, y2 + my)
        # Se recorta ANTES de enderezar: el bbox lo calculo FaceAnalyzer sobre
        # load_image_array, que decodifica con cv2.imdecode y no aplica la
        # orientacion EXIF. O sea, las coordenadas estan en la imagen sin girar
        # -- rotar primero las dejaria apuntando a cualquier lado. La
        # orientacion se lee aca porque crop() no la hereda.
        orient = orientacion(im, flip)
        cara = im.crop((nx1, ny1, nx2, ny2)).convert("RGB")
        cara = enderezar(cara, orient=orient)
        cara.thumbnail((tam_max, tam_max), Image.Resampling.LANCZOS)
        return cara
    except Exception:
        return None
