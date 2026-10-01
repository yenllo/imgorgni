"""
OCR sobre las fotos: lee carteles, banners, dorsales y documentos.

Es lo que más acerca los nombres de evento a algo real: un cartel que dice
"Cumpleaños de Marta" vale más que cualquier clasificación de escena.

Motor: RapidOCR (ONNX, sin binarios externos ni instalación de sistema).
Si no está instalado, todo el módulo se apaga solo y el resto del pipeline sigue.

    pip install rapidocr-onnxruntime
"""
import re
from typing import List, Optional, Tuple

import cv2
import numpy as np

# Palabras que aparecen en carteles de eventos y valen para nombrar
PISTAS_EVENTO = (
    "cumpleaños", "cumple", "feliz", "boda", "casamiento", "aniversario",
    "graduación", "egresados", "promoción", "titulo", "título",
    "navidad", "año nuevo", "fin de año", "bautismo", "comunión",
    "campeonato", "torneo", "copa", "liga", "final", "semifinal", "fecha",
    "festival", "concierto", "recital", "muestra", "expo", "feria",
    "aniversario", "despedida", "bienvenido", "bienvenida", "felicidades",
)

# Basura típica del OCR: marcas de agua, interfaces, ruido
RUIDO = re.compile(
    r"^(https?://|www\.|\d{1,2}:\d{2}$|[\W_]+$|[a-z]{1,2}$)", re.IGNORECASE)

_lector = None
_disponible: Optional[bool] = None


def disponible() -> bool:
    """True si RapidOCR se puede usar. Se evalúa una sola vez."""
    global _disponible, _lector
    if _disponible is not None:
        return _disponible
    try:
        from rapidocr_onnxruntime import RapidOCR
        _lector = RapidOCR()
        _disponible = True
    except Exception:
        _disponible = False
    return _disponible


def leer_texto(img: np.ndarray, min_confianza: float = 0.6,
               lado_max: int = 1280) -> List[Tuple[str, float]]:
    """
    Devuelve [(texto, confianza)] de los bloques legibles de la imagen.

    Se reduce la imagen antes de procesar: el OCR sobre 4000px es lentísimo y
    para carteles grandes 1280px alcanza de sobra.
    """
    if not disponible():
        return []
    try:
        h, w = img.shape[:2]
        if max(h, w) > lado_max:
            escala = lado_max / max(h, w)
            img = cv2.resize(img, (int(w * escala), int(h * escala)),
                             interpolation=cv2.INTER_AREA)
        resultado, _ = _lector(img)
    except Exception:
        return []

    if not resultado:
        return []

    salida = []
    for item in resultado:
        try:
            texto, conf = str(item[1]).strip(), float(item[2])
        except (IndexError, ValueError, TypeError):
            continue
        if conf < min_confianza or len(texto) < 3 or RUIDO.match(texto):
            continue
        salida.append((texto, round(conf, 3)))
    return salida


def texto_util(bloques: List[Tuple[str, float]], max_chars: int = 120) -> str:
    """Une los bloques en una línea, priorizando los más largos y confiables."""
    if not bloques:
        return ""
    ordenados = sorted(bloques, key=lambda b: (-len(b[0]) * b[1]))
    partes, total = [], 0
    for texto, _ in ordenados:
        if total + len(texto) > max_chars:
            break
        partes.append(texto)
        total += len(texto) + 1
    return " ".join(partes)


def _sin_tildes(s: str) -> str:
    """Normaliza para comparar: la gente escribe (y el OCR lee) sin tildes."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn").lower()


# Empieza en límite de palabra (así "final" no matchea dentro de "finalizara")
# pero admite sufijos, para que "graduacion" cubra "graduaciones" y
# "bienvenido" cubra "bienvenidos".
_RE_PISTAS = re.compile(
    r"\b(" + "|".join(re.escape(_sin_tildes(p)) for p in PISTAS_EVENTO) + r")\w{0,3}\b",
    re.IGNORECASE)

# Texto que aparece seguido pero no nombra ningún evento
_GENERICO = re.compile(
    r"^(sin\s*t[ií]tulo|untitled|documento|document|imagen|image|foto|photo|"
    r"captura|screenshot|whatsapp|instagram|facebook|youtube|google|"
    r"p[aá]gina\s*\d+|page\s*\d+)\W*$", re.IGNORECASE)


def pista_de_evento(bloques: List[Tuple[str, float]]) -> Optional[str]:
    """
    Extrae un nombre de evento del texto, si lo hay.

    Busca líneas con palabras de celebración usando límites de palabra, y
    descarta texto genérico. Devuelve la mejor candidata, lista para carpeta.
    """
    candidatos = []
    for texto, conf in bloques:
        if _GENERICO.match(texto.strip()):
            continue
        if not _RE_PISTAS.search(_sin_tildes(texto)):
            continue
        # Un cartel es una frase corta; un párrafo largo casi nunca es el nombre
        if len(texto) > 60:
            continue
        # Texto sin espacios suele ser OCR mal segmentado ("Elembarquefinaliza")
        if len(texto) > 15 and " " not in texto:
            continue
        letras = sum(c.isalpha() for c in texto)
        if letras < len(texto) * 0.6:      # mayormente números/símbolos
            continue
        candidatos.append((conf * (1.0 if len(texto) <= 40 else 0.7), texto))

    if not candidatos:
        return None
    mejor = max(candidatos)[1]
    limpio = re.sub(r"\s+", " ", mejor).strip(" .,-_|")
    return limpio[:60] or None
