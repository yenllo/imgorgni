"""
Análisis extendido por foto: escenas (CLIP zero-shot), GPS, calidad y hash perceptual.

Todo lo de acá es opcional y degrada solo: si falla una parte, el resto sigue.
Las funciones sin modelo (GPS/calidad/phash) son baratas y corren siempre.
"""
import json
import multiprocessing as mp
import queue
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Vocabulario de escenas para CLIP zero-shot.
# El prompt "a photo of X" es el que usa OpenAI en el paper; mejora bastante
# la precisión frente a pasar la etiqueta sola.
# ---------------------------------------------------------------------------

SCENE_VOCAB = [
    # Celebraciones y eventos
    "a birthday party", "a wedding ceremony", "a wedding reception", "a graduation ceremony",
    "a christmas celebration", "a new year party", "a baby shower", "a funeral",
    "a concert", "a music festival", "a sports game", "a parade",
    # Lugares al aire libre
    "a beach", "a mountain landscape", "a forest", "a lake", "a river", "a waterfall",
    "a desert", "a snowy landscape", "a park", "a garden", "a farm", "a vineyard",
    "a city street", "a city skyline", "a small town", "a historic building",
    "a church interior", "a museum", "a monument", "a bridge",
    # Interiores
    "a living room", "a kitchen", "a bedroom", "a restaurant interior", "a bar",
    "a cafe", "an office", "a classroom", "a hospital", "a store interior",
    "a hotel room", "a gym",
    # Actividades
    "people hiking", "people camping", "people swimming", "people dancing",
    "people cooking", "people eating a meal", "people playing music",
    "people playing soccer", "people cycling", "people skiing", "people surfing",
    "people at a picnic", "people working at computers", "people shopping",
    # Personas
    "a portrait of a person", "a selfie", "a group photo of people", "a family photo",
    "a photo of children playing", "a photo of a baby", "a couple together",
    "a crowd of people",
    # Animales
    "a dog", "a cat", "a horse", "a bird", "farm animals", "wild animals", "a zoo",
    "fish underwater",
    # Objetos y detalles
    "a plate of food", "a dessert or cake", "drinks and cocktails", "a birthday cake",
    "flowers", "a car", "a motorcycle", "a boat", "an airplane", "a bicycle",
    # Condiciones y estilo
    "a sunset", "a sunrise", "a night scene with lights", "fireworks",
    "a rainy day", "a foggy landscape", "a starry night sky",
    "an aerial drone view", "a macro close-up photo", "a black and white photo",
    # Documentos y descartes
    "a screenshot of a computer screen", "a document or receipt",
    "a blurry unusable photo", "a completely dark photo",
]


def _labels_from_vocab() -> List[str]:
    """Etiqueta corta y legible para el nombre del evento."""
    out = []
    for v in SCENE_VOCAB:
        s = v
        for prefix in ("a photo of ", "a ", "an ", "people "):
            if s.startswith(prefix):
                s = s[len(prefix):]
                break
        out.append(s)
    return out


SCENE_LABELS = _labels_from_vocab()

# Traducción para los nombres de carpeta. El pipeline trabaja con las etiquetas
# en inglés (así se entrenó CLIP); esto es solo la capa de presentación.
ETIQUETAS_ES = {
    "birthday party": "cumpleaños", "wedding ceremony": "boda",
    "wedding reception": "fiesta de boda", "graduation ceremony": "graduación",
    "christmas celebration": "navidad", "new year party": "año nuevo",
    "baby shower": "baby shower", "funeral": "funeral", "concert": "concierto",
    "music festival": "festival", "sports game": "partido", "parade": "desfile",
    "beach": "playa", "mountain landscape": "montaña", "forest": "bosque",
    "lake": "lago", "river": "río", "waterfall": "cascada", "desert": "desierto",
    "snowy landscape": "nieve", "park": "parque", "garden": "jardín",
    "farm": "campo", "vineyard": "viñedo", "city street": "calle",
    "city skyline": "ciudad", "small town": "pueblo",
    "historic building": "edificio histórico", "church interior": "iglesia",
    "museum": "museo", "monument": "monumento", "bridge": "puente",
    "living room": "living", "kitchen": "cocina", "bedroom": "dormitorio",
    "restaurant interior": "restaurante", "bar": "bar", "cafe": "café",
    "office": "oficina", "classroom": "sala de clases", "hospital": "hospital",
    "store interior": "tienda", "hotel room": "hotel", "gym": "gimnasio",
    "hiking": "trekking", "camping": "camping", "swimming": "natación",
    "dancing": "baile", "cooking": "cocinando", "eating a meal": "comida",
    "playing music": "música", "playing soccer": "fútbol", "cycling": "ciclismo",
    "skiing": "esquí", "surfing": "surf", "at a picnic": "picnic",
    "working at computers": "computadores", "shopping": "compras",
    "portrait of a person": "retrato", "selfie": "selfie",
    "group photo of people": "grupo", "family photo": "familia",
    "photo of children playing": "niños", "children playing": "niños",
    "photo of a baby": "bebé", "baby": "bebé", "couple together": "pareja",
    "crowd of people": "multitud",
    "dog": "perro", "cat": "gato", "horse": "caballo", "bird": "pájaro",
    "farm animals": "animales de granja", "wild animals": "animales salvajes",
    "zoo": "zoológico", "fish underwater": "peces",
    "plate of food": "plato de comida", "dessert or cake": "postre",
    "drinks and cocktails": "tragos", "birthday cake": "torta",
    "flowers": "flores", "car": "auto", "motorcycle": "moto", "boat": "bote",
    "airplane": "avión", "bicycle": "bicicleta",
    "sunset": "atardecer", "sunrise": "amanecer",
    "night scene with lights": "noche", "fireworks": "fuegos artificiales",
    "rainy day": "lluvia", "foggy landscape": "niebla",
    "starry night sky": "cielo estrellado", "aerial drone view": "vista aérea",
    "macro close-up photo": "macro", "black and white photo": "blanco y negro",
    "screenshot of a computer screen": "captura de pantalla",
    "document or receipt": "documento",
    "blurry unusable photo": "borrosa", "completely dark photo": "oscura",
}


def etiqueta_es(label: str) -> str:
    """Nombre legible en español para una escena; cae al original si falta."""
    return ETIQUETAS_ES.get(label.lower().strip(), label)


# ---------------------------------------------------------------------------
# 1. Escenas por CLIP zero-shot (usa el embedding visual que ya se calcula)
# ---------------------------------------------------------------------------

class SceneClassifier:
    """
    Clasifica escenas comparando el embedding visual de CLIP contra embeddings
    de texto pre-calculados. No necesita correr el text encoder en el pipeline:
    la matriz se genera una vez con `precompute_scene_embeddings.py`.
    """

    def __init__(self, embeddings_path: str):
        data = np.load(embeddings_path, allow_pickle=True)
        self.matrix = data["embeddings"].astype(np.float32)   # normalizada, SIN centrar
        self.labels = [str(x) for x in data["labels"]]
        # Se conserva por compatibilidad con archivos viejos; ya no se usa.
        self.mean = (data["mean"].astype(np.float32) if "mean" in data
                     else np.zeros(self.matrix.shape[1], dtype=np.float32))
        self.centrado = bool(data["centrado"]) if "centrado" in data else False

    @property
    def dim(self) -> int:
        return self.matrix.shape[1]

    def classify_batch(self, embeddings: List[Optional[List[float]]],
                       top_k: int = 3, min_score: float = 2.0) -> List[List[str]]:
        """
        Devuelve las top_k escenas que se destaquen claramente del resto.

        `min_score` es un z-score, no una probabilidad: cuántas desviaciones
        estándar por encima de la similitud media debe estar una etiqueta para
        considerarse detectada.

        Se usa z-score y no softmax porque la escala de similitud depende del
        modelo: CLIP B-32 da cosenos ~0.29 y SigLIP SO400M ~0.03 para el mismo
        acierto. Un umbral absoluto funciona con uno y descarta todo con el otro;
        el z-score compara cada etiqueta contra las demás y sirve para ambos.
        """
        results = []
        for emb in embeddings:
            if not emb or len(emb) != self.dim:
                results.append([])
                continue
            v = np.asarray(emb, dtype=np.float32)
            if self.centrado:
                v = v - self.mean
            n = float(np.linalg.norm(v))
            if n <= 1e-8:
                results.append([])
                continue
            sims = self.matrix @ (v / n)
            desvio = float(sims.std())
            if desvio <= 1e-8:
                results.append([])
                continue
            z = (sims - float(sims.mean())) / desvio
            idx = np.argsort(-z)[:top_k]
            results.append([self.labels[i] for i in idx if z[i] >= min_score])
        return results


# ---------------------------------------------------------------------------
# 1b. Escena -> categoría temática de carpeta
# Los nombres replican la estructura que ya usás a mano en E:\Fotos_Organizadas
# ---------------------------------------------------------------------------

CATEGORY_RULES: List[Tuple[str, List[str]]] = [
    # (carpeta, escenas que la disparan). El orden importa: gana la primera.
    ("Fotos Astro", ["starry night sky", "fireworks"]),
    ("Fotos eventos", ["birthday party", "wedding ceremony", "wedding reception",
                       "graduation ceremony", "christmas celebration", "new year party",
                       "baby shower", "concert", "music festival", "parade",
                       "at a picnic", "birthday cake", "funeral"]),
    ("Fotos deportes", ["sports game", "playing soccer", "cycling", "skiing", "surfing",
                        "swimming", "a gym", "hiking"]),
    ("Fotos escolares", ["classroom", "a museum"]),
    ("Fotos animales", ["dog", "cat", "horse", "bird", "farm animals", "wild animals",
                        "a zoo", "fish underwater"]),
    ("Fotos plantas", ["flowers", "a garden", "a forest", "a vineyard", "a farm"]),
    ("Fotos autos", ["car", "motorcycle", "boat", "airplane", "bicycle"]),
    ("Fotos paisajes", ["beach", "mountain landscape", "lake", "river", "waterfall",
                        "desert", "snowy landscape", "sunset", "sunrise",
                        "city skyline", "aerial drone view", "foggy landscape",
                        "a park", "camping"]),
    ("Fotos comida", ["plate of food", "dessert or cake", "drinks and cocktails",
                      "restaurant interior", "cooking", "eating a meal", "a cafe"]),
    ("Fotos personas", ["portrait of a person", "selfie", "group photo of people",
                        "family photo", "children playing", "a baby", "couple together",
                        "crowd of people", "dancing", "playing music"]),
    ("Fotos descartes", ["blurry unusable photo", "completely dark photo",
                         "screenshot of a computer screen", "document or receipt"]),
]

DEFAULT_CATEGORY = "Fotos varias"


def scene_to_category(scene_tags: List[str], quality: Optional[Dict] = None,
                      dispositivo: str = "") -> str:
    """
    Primera categoría cuya regla matchee alguna escena detectada.

    Dos guardas aprendidas del ensayo con fotos reales:
      - Una foto objetivamente mala va a descartes, sin importar la escena:
        CLIP etiqueta con alta confianza imágenes casi negras como
        "starry night sky" (250 falsos positivos en 1.166 fotos).
      - Astro exige cámara dedicada: la astrofotografía real no sale
        de un celular a pulso, y así no se cuelan las nocturnas fallidas.
    """
    if quality and is_low_quality(quality):
        return "Fotos descartes"

    if not scene_tags:
        return DEFAULT_CATEGORY

    low = [s.lower() for s in scene_tags]
    for category, triggers in CATEGORY_RULES:
        if category == "Fotos Astro" and dispositivo == "telefono":
            continue
        for trig in triggers:
            if any(trig in s for s in low):
                return category
    return DEFAULT_CATEGORY


def categorize_event(all_scene_tags: List[List[str]], has_faces: bool = False,
                     qualities: Optional[List[Dict]] = None,
                     dispositivos: Optional[List[str]] = None) -> str:
    """
    Categoría de un evento entero por voto de sus fotos.

    Vota sobre la categoría YA calculada por foto, así hereda las guardas de
    calidad y dispositivo en vez de duplicarlas.
    Si la mayoría tiene caras, gana 'Fotos personas' salvo que sea un evento marcado.
    """
    from collections import Counter
    n = len(all_scene_tags)
    qualities = qualities or [None] * n
    dispositivos = dispositivos or [""] * n
    votes = Counter(scene_to_category(tags, q, d)
                    for tags, q, d in zip(all_scene_tags, qualities, dispositivos))
    # Descartes no debe definir un evento entero: es una propiedad por foto
    votes.pop("Fotos descartes", None)
    votes.pop(DEFAULT_CATEGORY, None)  # no dejar que el default gane por descarte
    if not votes:
        return "Fotos personas" if has_faces else DEFAULT_CATEGORY
    top, count = votes.most_common(1)[0]
    # Un evento social con gente pesa más que el paisaje de fondo
    if has_faces and top in ("Fotos paisajes", "Fotos varias") and votes.get("Fotos eventos", 0) > 0:
        return "Fotos eventos"
    return top


# ---------------------------------------------------------------------------
# 1c. Teléfono vs cámara (desde el EXIF, sin modelo de IA)
# ---------------------------------------------------------------------------

# Marcas que solo fabrican teléfonos/tablets
MARCAS_TELEFONO = (
    "apple", "iphone", "ipad", "samsung", "xiaomi", "redmi", "poco",
    "huawei", "honor", "motorola", "moto ", "google", "pixel", "oneplus",
    "oppo", "vivo", "realme", "nokia", "lge", "lg electronics", "htc",
    "zte", "meizu", "lenovo", "infinix", "tecno", "alcatel", "tcl",
    "blackberry", "microsoft", "lumia", "asus", "zenfone",
)

# Marcas de cámaras dedicadas
MARCAS_CAMARA = (
    "canon", "nikon", "fujifilm", "fuji", "panasonic", "lumix", "olympus",
    "om digital", "pentax", "ricoh", "leica", "sigma", "hasselblad",
    "phase one", "mamiya", "kodak", "casio", "gopro", "dji", "eastman",
    "minolta", "konica", "epson", "hp ", "agfa", "polaroid",
)

# Sony fabrica ambos: sus cámaras usan estos prefijos de modelo
SONY_CAMARA = ("ilce", "dsc-", "slt-", "nex-", "dslr", "zv-", "a7", "a6", "a9", "a1")

# Nombres de archivo que solo genera una cámara dedicada:
#   _DSC0001 (Sony)   DSC_0001 (Nikon)   DSCF0001 (Fuji)   DSCN0001 (Coolpix)
#   IMGP0001 (Pentax) PICT0001           CRW_0001/MVI_0001 (Canon)
# Sirve como último recurso cuando el archivo perdió el EXIF (edición, export).
_CAMARA_NAME_RE = re.compile(
    r"^(_?DSC[_NF]?\d|CRW_\d|MVI_\d|PICT\d|IMGP\d|R\d{7})", re.IGNORECASE)


def clasificar_dispositivo(camera_model: Optional[str], file_path: str = "") -> str:
    """
    Devuelve 'telefono', 'camara' o 'desconocido'.

    Se basa en el EXIF (marca + modelo). Si no hay EXIF, cae a heurística de
    nombre de archivo, que delata las fotos de WhatsApp y las capturas.
    """
    modelo = (camera_model or "").lower().strip()

    if modelo:
        # Sony primero: es la única marca ambigua
        if "sony" in modelo:
            return "camara" if any(k in modelo for k in SONY_CAMARA) else "telefono"
        if any(m in modelo for m in MARCAS_TELEFONO):
            return "telefono"
        if any(m in modelo for m in MARCAS_CAMARA):
            return "camara"

    # Sin EXIF útil: los RAW son siempre de cámara dedicada
    ext = Path(file_path).suffix.lower()
    if ext in {".cr2", ".arw", ".nef", ".rw2", ".raf", ".raw"}:
        return "camara"

    # Nombres típicos de teléfono: IMG-20230101-WA0001, Screenshot_..., PXL_...
    from utils_images import _MOBILE_NAME_RE
    nombre = Path(file_path).name
    if _MOBILE_NAME_RE.search(nombre) or nombre.lower().startswith(("pxl_", "img-", "vid-")):
        return "telefono"

    # Y la simétrica: nombres que SOLO produce una cámara dedicada. Sin esto, una
    # _DSC0470-2.jpg exportada sin EXIF caía en 'desconocido', y el export manda
    # todo lo que no sea 'camara' a la carpeta de teléfono.
    # IMG_ queda afuera a propósito: lo usan Canon y el iPhone por igual.
    if _CAMARA_NAME_RE.match(nombre):
        return "camara"

    return "desconocido"


# ---------------------------------------------------------------------------
# 2. GPS desde EXIF (sin modelo, casi gratis)
# ---------------------------------------------------------------------------

def _dms_to_decimal(dms, ref: str) -> Optional[float]:
    try:
        parts = [float(x.num) / float(x.den) for x in dms.values]
        while len(parts) < 3:
            parts.append(0.0)
        dec = parts[0] + parts[1] / 60.0 + parts[2] / 3600.0
        if ref in ("S", "W"):
            dec = -dec
        return round(dec, 6)
    except Exception:
        return None


def _gps_worker_loop(entrada: "mp.Queue", salida: "mp.Queue"):
    """Vive en un proceso aparte, sirve pedidos uno por uno hasta que lo maten."""
    import exifread
    while True:
        fp = entrada.get()
        if fp is None:
            return
        try:
            with open(fp, "rb") as f:
                tags = exifread.process_file(f, details=False)
            lat = tags.get("GPS GPSLatitude")
            lat_ref = tags.get("GPS GPSLatitudeRef")
            lon = tags.get("GPS GPSLongitude")
            lon_ref = tags.get("GPS GPSLongitudeRef")
            if lat and lon and lat_ref and lon_ref:
                salida.put((_dms_to_decimal(lat, str(lat_ref)),
                            _dms_to_decimal(lon, str(lon_ref))))
                continue
        except Exception:
            pass
        salida.put((None, None))


class _TrabajadorGPS:
    """Un solo proceso reutilizado entre llamadas: crear un proceso por foto
    (17 mil veces) tiene su propio costo. Si se cuelga, se mata de verdad con
    terminate() -- a diferencia de un hilo, esto SI libera la CPU y el archivo
    al toque -- y se levanta uno nuevo para la siguiente foto."""

    def __init__(self, worker=_gps_worker_loop):
        self.worker = worker      # inyectable: los tests usan uno falso y lento
        self.proc = None
        self.entrada = None
        self.salida = None

    def _asegurar(self):
        if self.proc is None or not self.proc.is_alive():
            self.entrada = mp.Queue()
            self.salida = mp.Queue()
            self.proc = mp.Process(target=self.worker,
                                   args=(self.entrada, self.salida), daemon=True)
            self.proc.start()

    def leer(self, fp: str, timeout: float):
        self._asegurar()
        self.entrada.put(fp)
        try:
            return self.salida.get(timeout=timeout)
        except queue.Empty:
            # se atasco en este archivo (IFD GPS corrupto): se mata de una y
            # se reemplaza, en vez de esperarlo o dejarlo zombie de fondo.
            self.proc.terminate()
            self.proc.join(timeout=2)
            self.proc = None
            return None, None


_gps_trabajador: Optional[_TrabajadorGPS] = None


def read_gps(fp: str, timeout: float = 0.4) -> Tuple[Optional[float], Optional[float]]:
    """Devuelve (lat, lon) de EXIF; si no hay, del sidecar de Google Takeout.

    exifread puede quedar atascado varios segundos en un IFD GPS corrupto:
    offsets mal formados lo mandan a una recursion cara entre _process_tag y
    dump_ifd (asi salian los "Possibly corrupted field Tag..." en el log, y
    la Fase 2 caia de ~0.15 a 5+ s/foto en material re-procesado por OneDrive
    con el bloque GPS del EXIF dañado -- y no fue un archivo aislado, sino
    buena parte de esa carpeta).

    Un hilo con timeout no alcanza: si se lo abandona, sigue vivo peleando por
    el GIL con el resto del pipeline, y se acumulan mas y mas zombies cuanto
    mas corre (medido con py-spy: 9 hilos atascados en el primer minuto). Un
    PROCESO aparte si se puede matar de verdad con terminate(), liberando todo.
    """
    global _gps_trabajador
    if _gps_trabajador is None:
        _gps_trabajador = _TrabajadorGPS()
    lat, lon = _gps_trabajador.leer(fp, timeout)
    if lat is not None:
        return lat, lon

    from utils_images import read_takeout_sidecar
    _, lat, lon = read_takeout_sidecar(fp)
    return lat, lon


class PlaceResolver:
    """Coordenadas -> nombre de lugar, offline. Cachea por celda de ~11 km."""

    def __init__(self):
        self._cache: Dict[Tuple[float, float], Optional[str]] = {}
        self._rg = None
        try:
            import reverse_geocoder
            self._rg = reverse_geocoder
        except Exception:
            pass

    def resolve(self, lat: Optional[float], lon: Optional[float]) -> Optional[str]:
        if lat is None or lon is None or self._rg is None:
            return None
        key = (round(lat, 1), round(lon, 1))  # ~11 km: sobra para nombrar un evento
        if key in self._cache:
            return self._cache[key]
        try:
            hit = self._rg.search((lat, lon), mode=1, verbose=False)[0]
            # El campo 'name' a veces trae la jerarquía entera
            # ("Villa Presidente Frei, Nunoa, Santiago, Chile"): para una carpeta
            # alcanza la localidad, y si es un barrio se prefiere la ciudad (admin1).
            crudo = (hit.get("name") or "").strip()
            partes = [p.strip() for p in crudo.split(",") if p.strip()]
            ciudad = partes[0] if partes else ""
            if len(partes) > 2:
                ciudad = partes[-2]          # la ciudad, no el barrio
            elif not ciudad:
                ciudad = (hit.get("admin1") or "").strip()
            pais = (hit.get("cc") or "").strip()
            place = f"{ciudad}, {pais}".strip(", ")[:40] or None
        except Exception:
            place = None
        self._cache[key] = place
        return place


# ---------------------------------------------------------------------------
# 3. Calidad de imagen (sin modelo)
# ---------------------------------------------------------------------------

def quality_metrics(img: np.ndarray) -> Dict[str, float]:
    """
    sharpness: varianza del Laplaciano (mayor = más nítida).
    brightness: media 0-255. contrast: desvío estándar. w/h: dimensiones reales.
    Sobre imagen reducida: la nitidez relativa se conserva y es ~10x más rápido.
    """
    try:
        h, w = img.shape[:2]
        small = cv2.resize(img, (512, 512), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        return {
            "sharpness": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2),
            "brightness": round(float(gray.mean()), 2),
            "contrast": round(float(gray.std()), 2),
            "w": int(w), "h": int(h),
        }
    except Exception:
        return {"sharpness": 0.0, "brightness": 0.0, "contrast": 0.0, "w": 0, "h": 0}


# Nombres típicos de assets web / iconos / archivos de sistema
_NO_FOTO_RE = re.compile(
    r"(^\$[A-Z0-9]{6,}|^thumb|_thumb|icon|logo|banner|bullet|background|"
    r"header|footer|sprite|favicon|placeholder|avatar|-mv2\.|^file\.|"
    r"^image_?\d{0,3}\.|^download\.|^untitled\.|screenshot.*\.png$|"
    r"[0-9a-f]{24,})", re.IGNORECASE)  # hash largo = CDN web (Wix, etc.)


def es_no_foto(camera_model: Optional[str], file_path: str,
               quality: Optional[Dict] = None) -> bool:
    """
    True si el archivo es un asset web, icono o imagen de sistema en vez de
    una foto personal. Solo marca cuando NO hay EXIF de cámara: una foto real
    con metadatos nunca se descarta por esto.
    """
    if camera_model:            # tiene EXIF de cámara -> es una foto real
        return False
    if Path(file_path).suffix.lower() in SUPPORTED_RAW_EXTS_LOCAL:
        return False

    if quality:
        w, h = int(quality.get("w", 0)), int(quality.get("h", 0))
        # Menos de 0.3 MP sin EXIF: icono, miniatura o gráfico web
        if 0 < w * h < 300_000:
            return True

    return bool(_NO_FOTO_RE.search(Path(file_path).name))


SUPPORTED_RAW_EXTS_LOCAL = {".cr2", ".arw", ".nef", ".rw2", ".raf", ".raw", ".dng"}


def is_low_quality(m: Dict[str, float]) -> bool:
    """
    Umbrales conservadores: marcar solo lo claramente inservible.
    brightness < 20 es prácticamente negro (medido sobre fotos reales:
    las nocturnas fallidas del celular caen entre 1 y 15).
    """
    # ponytail: umbrales fijos; si marca demasiadas buenas, subir/bajar acá
    return (m.get("sharpness", 0) < 25.0
            or m.get("brightness", 0) < 20.0
            or m.get("brightness", 0) > 245.0)


# ---------------------------------------------------------------------------
# 4. Hash perceptual (detecta ráfagas y casi-duplicados)
# ---------------------------------------------------------------------------

def phash(img: np.ndarray, hash_size: int = 8) -> Optional[str]:
    """DCT hash de 64 bits en hex. Imágenes similares -> hashes cercanos."""
    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(gray, (hash_size * 4, hash_size * 4),
                             interpolation=cv2.INTER_AREA).astype(np.float32)
        dct = cv2.dct(resized)[:hash_size, :hash_size]
        med = np.median(dct[1:, 1:])  # ignorar el término DC
        bits = (dct > med).flatten()
        return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"
    except Exception:
        return None


def hamming(h1: str, h2: str) -> int:
    """Distancia entre dos phash hex. <=5 suele ser la misma escena."""
    try:
        return bin(int(h1, 16) ^ int(h2, 16)).count("1")
    except Exception:
        return 64


# ---------------------------------------------------------------------------
# 5. Caras (opcional; requiere `pip install insightface`)
# ---------------------------------------------------------------------------

class FaceAnalyzer:
    """
    Detecta caras y extrae embeddings ArcFace de 512 dims con InsightFace.

    Licencia: los packs buffalo_* de InsightFace son para uso NO COMERCIAL
    (investigación/personal). Ver https://github.com/deepinsight/insightface
    """

    def __init__(self, use_gpu: bool = True, det_size: int = 640):
        from insightface.app import FaceAnalysis
        providers = (["CUDAExecutionProvider", "CPUExecutionProvider"] if use_gpu
                     else ["CPUExecutionProvider"])
        self.app = FaceAnalysis(name="buffalo_l", providers=providers)
        self.app.prepare(ctx_id=0 if use_gpu else -1, det_size=(det_size, det_size))

    def analyze(self, img: np.ndarray, min_lado_rel: float = 0.05,
                min_det_score: float = 0.7) -> List[Dict]:
        """
        Devuelve [{embedding, bbox, det_score, age, gender}] por cara.

        Descarta caras de fondo: por debajo del 5% del lado corto de la imagen
        el embedding es demasiado pobre y todas terminan pareciéndose entre sí,
        lo que fusiona desconocidos en una "persona" fantasma. Mismo criterio
        de score mínimo que usa Immich (0.7).
        """
        try:
            faces = self.app.get(img)
        except Exception:
            return []
        lado_corto = min(img.shape[:2])
        minimo_px = lado_corto * min_lado_rel
        out = []
        for f in faces:
            emb = getattr(f, "normed_embedding", None)
            if emb is None:
                continue
            if float(getattr(f, "det_score", 1.0)) < min_det_score:
                continue
            x1, y1, x2, y2 = (float(v) for v in f.bbox)
            if min(x2 - x1, y2 - y1) < minimo_px:
                continue
            out.append({
                "embedding": [round(float(x), 5) for x in emb],
                "bbox": [int(x) for x in f.bbox],
                "det_score": round(float(f.det_score), 3),
                "age": int(f.age) if getattr(f, "age", None) is not None else None,
                "gender": int(f.gender) if getattr(f, "gender", None) is not None else None,
            })
        return out


def cluster_faces(embeddings: List[List[float]], eps: float = 0.35,
                  min_samples: int = 3) -> List[int]:
    """
    Agrupa caras en personas. Devuelve una etiqueta por cara (-1 = sin agrupar).
    eps sobre distancia coseno: DBSCAN encadena transitivamente (si A~B y B~C
    quedan en el mismo cluster aunque A y C no se parezcan en nada), asi que a
    partir de ~55k caras 0.55 colapsaba: un cluster de 9908 caras con similitud
    media 0.06 (gente totalmente distinta). Con 0.35 el cluster mas grande baja
    a ~1200 caras con similitud media >0.55 (una sola persona real).
    """
    if not embeddings:
        return []
    from sklearn.cluster import DBSCAN
    X = np.asarray(embeddings, dtype=np.float32)
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    X = X / np.maximum(norms, 1e-8)
    labels = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine").fit_predict(X)
    return [int(x) for x in labels]
