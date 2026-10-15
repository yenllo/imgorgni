"""
Fase 5: Detector minucioso de duplicados.

Pensado para fototecas dispersas entre HDD, SSD, notebook, Google Drive y OneDrive,
donde la misma foto aparece varias veces con distinto nombre, tamaño o formato.

Detecta cuatro clases de duplicado:
  1. EXACTO      - mismo SHA256 (byte a byte)
  2. RECOMPRIMIDO- misma imagen resubida/redimensionada (pHash distancia <= 2)
  3. SIMILAR     - misma escena, edición leve o ráfaga (pHash <= umbral)
  4. RAW+JPG     - mismo nombre base y distinta extensión (IMG_1234.CR2 + .JPG)

NUNCA toca archivos: solo escribe duplicados.csv. Mover a cuarentena es trabajo
del curador (web_curador.py /duplicados), que además actualiza la BD — el
--mover que había acá movía los archivos dejando la base apuntando a rutas
muertas.

    python 5_duplicados.py                      # reporte (no toca archivos)
    python 5_duplicados.py --umbral 4           # más estricto
    python 5_duplicados.py --incluir-raw-jpg    # tratar RAW+JPG como duplicado
"""
import argparse
import csv
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from db import get_conn, verificar_disco_montado
from utils_images import SUPPORTED_RAW_EXTS
from utils_log import Spinner, log_step

# Carpetas cuyo contenido suele ser una copia degradada (WhatsApp, descargas, caché)
RUTAS_SOSPECHOSAS = ("whatsapp", "download", "descarga", "telegram", "cache",
                     "temp", "papelera", "recycle", "copia", "copy", " (1)",
                     "screenshot", "captura", "thumbnail", "miniatura",
                     "_duplicados_contenido", "carpetasexternas", "_cuarentena")

# La copia que ya está en el árbol organizado es la buena por definición: es la
# que apuntan los eventos y la que exporta la Fase 4. Sin esto, entre dos JPEG
# de la misma resolución ganaba el de ruta más corta — que resultó ser casi
# siempre el de Fotos_F_rescatadas, justo el que tiene 22,7% de daño.
RUTAS_BUENAS = ("fotos_organizadas",)

# Lo que ya se mandó a cuarentena está resuelto: volver a emparejarlo genera
# pares nuevos entre un archivo vivo y uno que ya está en la papelera.
CUARENTENA = r"K:\_duplicados_cuarentena"

# Lado mínimo para que el pHash sea significativo. Por debajo (iconos, favicons)
# el hash es degenerado y produce falsos positivos contra cualquier imagen.
MIN_LADO_PHASH = 64


def es_raw(path: str) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_RAW_EXTS


def penalizacion_ruta(path: str) -> int:
    """Cuántos indicios hay de que esta copia es la degradada. Negativo si está
    en el árbol organizado, que es donde vive la copia buena."""
    low = path.lower()
    return (sum(1 for s in RUTAS_SOSPECHOSAS if s in low)
            - sum(1 for s in RUTAS_BUENAS if s in low))


def en_cuarentena(path: str) -> bool:
    return (path or "").lower().startswith(CUARENTENA.lower())


def dimensiones(path: str) -> Tuple[int, int]:
    """(ancho, alto) reales. RAW vía rawpy (tamaño del sensor, sin decodificar
    píxeles): PIL a veces "abre" un RAW igual, pero lee el IFD de una miniatura
    o preview embebido en vez de la imagen principal -- 100% de error en una
    muestra de .NEF/.DNG, y .ARW ni siquiera lo abre (quedaba en (0,0), el peor
    caso: perdía cualquier comparación de "más píxeles" contra otro archivo).
    Para el resto, solo lee el encabezado. (0,0) si no se puede."""
    try:
        if es_raw(path):
            import rawpy
            with rawpy.imread(path) as raw:
                return int(raw.sizes.width), int(raw.sizes.height)
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        return (0, 0)


# Mas alto = mejor candidato a conservar. Sano gana; y si hay que elegir entre
# dos rotos, la que solo perdio el marcador de fin le gana a la que perdio imagen.
_RANGO_DANO = {0: 2, 2: 1, 1: 0}


class Candidato:
    __slots__ = ("photo_id", "path", "size", "capture_date", "camera_model",
                 "phash", "danado", "existe", "_dims")

    def __init__(self, photo_id, path, size, capture_date, camera_model, phash,
                 danado=0, existe=True):
        self.photo_id = photo_id
        self.path = path
        self.size = size or 0
        self.capture_date = capture_date
        self.camera_model = camera_model
        self.phash = phash
        # 0 sano, 1 perdió datos, 2 solo le falta el marcador de fin. Se guarda
        # el nivel y no un bool: entre una copia que perdió imagen y otra a la
        # que solo le falta el terminador, hay que quedarse con la segunda.
        self.danado = int(danado or 0)
        self.existe = bool(existe)
        self._dims = None

    @property
    def dims(self) -> Tuple[int, int]:
        if self._dims is None:
            self._dims = dimensiones(self.path)
        return self._dims

    @property
    def pixeles(self) -> int:
        w, h = self.dims
        return w * h

    def puntaje(self) -> tuple:
        """
        Mayor es mejor. El orden de los criterios define qué copia se conserva:
          0. Está en el disco (una ruta muerta no se conserva nunca)
          1. No está dañada (esto manda sobre todo lo demás: conservar la copia
             rota y descartar la sana fue el error de 594 pares en agosto 2026)
          2. RAW sobre JPG (tiene toda la información del sensor)
          3. Más píxeles (no es una versión reducida)
          4. Tiene EXIF de cámara (las copias de WhatsApp/Drive lo pierden)
          5. Ruta menos sospechosa (y bonus si está en el árbol organizado)
          6. Archivo más grande (menos recomprimido)
          7. Ruta más corta, como desempate estable
        """
        return (
            1 if self.existe else 0,
            _RANGO_DANO.get(self.danado, 0),
            1 if es_raw(self.path) else 0,
            self.pixeles,
            1 if self.camera_model else 0,
            -penalizacion_ruta(self.path),
            self.size,
            -len(self.path),
        )


def hamming(h1: int, h2: int) -> int:
    return bin(h1 ^ h2).count("1")


def agrupar_por_phash(candidatos: List[Candidato], umbral: int) -> List[List[Candidato]]:
    """
    Agrupa por similitud perceptual usando multi-index hashing.

    Comparar todos contra todos sería 140k^2 = 19.600 millones de pares: inviable.
    En su lugar el hash de 64 bits se parte en 8 bloques de 8 bits. Si dos hashes
    difieren en <= 7 bits, por el principio del palomar al menos un bloque coincide
    exacto, así que basta comparar dentro de cada bucket.

    # ponytail: garantiza recall completo hasta distancia 7; con umbral >7 puede
    # perder algún par. Si alguna vez hace falta más, subir a 16 bloques de 4 bits.
    """
    if umbral > 7:
        log_step(f"⚠️ Umbral {umbral} > 7: puede escaparse algún par lejano")

    valores: Dict[int, List[Candidato]] = defaultdict(list)
    for c in candidatos:
        try:
            valores[int(c.phash, 16)].append(c)
        except (ValueError, TypeError):
            continue

    # Índice: (nro de bloque, valor del bloque) -> hashes que lo contienen
    buckets: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    for h in valores:
        for b in range(8):
            buckets[(b, (h >> (b * 8)) & 0xFF)].append(h)

    # Union-Find para fusionar grupos transitivos (A~B, B~C => A,B,C juntos)
    padre: Dict[int, int] = {h: h for h in valores}

    def find(x: int) -> int:
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    def union(a: int, b: int):
        ra, rb = find(a), find(b)
        if ra != rb:
            padre[rb] = ra

    # ponytail: nada de "vistos" global -- con buckets de miles de fotos (deportes,
    # cielo, escenas parecidas) esa set de pares llegaba a cientos de millones de
    # tuplas (~19+ GB) y reventaba la RAM. Vecinos por hash, locales y descartables:
    # el doble cómputo de hamming() (barato, solo XOR+popcount) sale más barato
    # que mantener ese set vivo.
    for h in valores:
        vecinos = set()
        for b in range(8):
            vecinos.update(buckets[(b, (h >> (b * 8)) & 0xFF)])
        vecinos.discard(h)
        for otro in vecinos:
            if hamming(h, otro) <= umbral:
                union(h, otro)

    grupos: Dict[int, List[Candidato]] = defaultdict(list)
    for h, items in valores.items():
        grupos[find(h)].extend(items)

    return [g for g in grupos.values() if len(g) > 1]


def agrupar_raw_jpg(candidatos: List[Candidato]) -> List[List[Candidato]]:
    """Agrupa por nombre base: IMG_1234.CR2 e IMG_1234.JPG son la misma toma."""
    por_nombre: Dict[str, List[Candidato]] = defaultdict(list)
    for c in candidatos:
        por_nombre[Path(c.path).stem.lower()].append(c)
    # Solo interesa si mezcla RAW y no-RAW; si no, ya lo cubre el pHash
    return [g for g in por_nombre.values()
            if len(g) > 1 and any(es_raw(c.path) for c in g)
            and any(not es_raw(c.path) for c in g)]


INDICADORES_DEPORTE = ("deporte", "ligaexscuola")


def es_deporte(path: str) -> bool:
    low = path.lower()
    return any(ind in low for ind in INDICADORES_DEPORTE)


def _numero_secuencia(path: str) -> Optional[str]:
    """Numero de serie que pone la camara en el nombre (DSC07299.ARW ->
    '07299'), sobrevive a sufijos de export/edicion (_editado, (2), etc)."""
    nombre = Path(path).name
    m = re.search(r"(\d{3,})", nombre)
    return m.group(1) if m else None


def clasificar(grupo: List[Candidato]) -> str:
    """RECOMPRIMIDO = misma foto resubida/redimensionada: exige distancia de
    pHash <=2 Y mismo numero de secuencia de camara en el nombre. Con
    contenido muy repetitivo (rafagas de deporte, misma cancha/luz/encuadre)
    el pHash solo confunde tomas distintas del mismo partido como si fueran
    recompresiones. La fecha EXIF no sirve de desempate porque esta camara
    no graba sub-segundos (DateTimeOriginal es solo por segundo, y una
    rafaga tira varios frames por segundo); el numero de serie de la camara
    si distingue frames consecutivos de verdad."""
    hashes = {c.phash for c in grupo}
    numeros = {_numero_secuencia(c.path) for c in grupo}
    mismo_numero = len(numeros) <= 1 and None not in numeros
    if len(hashes) == 1:
        return "RECOMPRIMIDO" if mismo_numero else "SIMILAR"
    dists = [hamming(int(a.phash, 16), int(b.phash, 16))
             for i, a in enumerate(grupo) for b in grupo[i + 1:]
             if a.phash and b.phash]
    return "RECOMPRIMIDO" if dists and max(dists) <= 2 and mismo_numero else "SIMILAR"


def _columnas(conn, tabla: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({tabla})")}


def _filtro_vivos(conn) -> Tuple[str, list]:
    """WHERE que deja fuera lo que ya no participa: lo mandado al basurero por
    el curador y lo que ya está en la carpeta de cuarentena. Las columnas se
    chequean porque las crea el curador, no el pipeline."""
    cols = _columnas(conn, "files")
    where, args = [], []
    if "borrado" in cols:
        where.append("COALESCE(f.borrado,0)=0")
    where.append("LOWER(SUBSTR(f.file_path,1,?)) <> ?")
    args += [len(CUARENTENA), CUARENTENA.lower()]
    return " AND ".join(where), args


def run(config_path: str = "config.json", umbral: int = 5,
        incluir_raw_jpg: bool = False, salida_csv: str = "duplicados.csv"):
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # con el disco caido, TODO parece duplicado ausente: se corta antes
    verificar_disco_montado(cfg["root_dir"])

    conn = get_conn(cfg["db_path"])
    vivos, args_vivos = _filtro_vivos(conn)
    # files.danado la llena cargar_integridad.py con el barrido de verificar_
    # integridad.py / verificar_raw.py. Si nunca se corrió, nadie está dañado.
    col_danado = "f.danado" if "danado" in _columnas(conn, "files") else "0"

    log_step("=" * 60)
    log_step("FASE 5: DETECCIÓN DE DUPLICADOS")
    log_step(f"  Umbral de similitud: {umbral} bits (menor = más estricto)")
    log_step("  Modo: SOLO REPORTE (no toca archivos)")
    log_step("=" * 60)

    # --- 1. Duplicados exactos por SHA256 ---
    spinner = Spinner("Buscando duplicados exactos (SHA256)...")
    spinner.start()
    exactos = defaultdict(list)
    for pid, path, sha, dan in conn.execute(
            f"""SELECT f.id, f.file_path, f.file_hash_sha256, {col_danado}
                FROM files f WHERE f.file_hash_sha256 IS NOT NULL AND {vivos}""",
            args_vivos):
        exactos[sha].append((pid, path, dan))
    grupos_exactos = {k: v for k, v in exactos.items() if len(v) > 1}
    n_exactos = sum(len(v) - 1 for v in grupos_exactos.values())
    spinner.stop(f"Duplicados exactos: {n_exactos:,} archivos en {len(grupos_exactos):,} grupos")

    # --- 2. Cargar candidatos con pHash ---
    spinner2 = Spinner("Cargando hashes perceptuales...")
    spinner2.start()
    candidatos = []
    descartados_min = 0
    for pid, path, cap, cam, ph, qj, dan in conn.execute(f"""
            SELECT f.id, f.file_path, f.capture_date, f.camera_model, pf.phash,
                   pf.quality_json, {col_danado}
            FROM files f JOIN photo_features pf ON f.id = pf.photo_id
            WHERE pf.phash IS NOT NULL AND {vivos}""", args_vivos):
        # El pHash de una imagen diminuta es degenerado: un icono de 6x6 matchea
        # con cualquier cosa. Se excluyen del emparejamiento perceptual.
        try:
            q = json.loads(qj or "{}")
            w, h = int(q.get("w", 0)), int(q.get("h", 0))
            if 0 < min(w, h) < MIN_LADO_PHASH:
                descartados_min += 1
                continue
        except (json.JSONDecodeError, ValueError):
            pass
        try:
            size, existe = Path(path).stat().st_size, True
        except OSError:
            size, existe = 0, False
        c = Candidato(pid, path, size, cap, cam, ph, danado=dan, existe=existe)
        # quality_json trae w/h de lo que Fase 2 decodificó -- para RAW eso es
        # el preview embebido o una revelada a mitad de tamaño (utils_images.
        # _raw_preview / load_image_array), nunca el sensor completo. Confiarle
        # el pixelaje a un RAW lo hace perder por "menos píxeles" contra un
        # JPG que sí se leyó entero. Solo se pre-llena para no-RAW.
        if w and h and not es_raw(path):
            c._dims = (w, h)  # evita reabrir el archivo con PIL más adelante
        candidatos.append(c)
    spinner2.stop(f"{len(candidatos):,} fotos con pHash"
                  + (f" ({descartados_min} miniaturas excluidas)" if descartados_min else ""))

    if not candidatos:
        log_step("⚠️ No hay pHash en la BD. Corré la Fase 2 con enable_phash=true.")
        conn.close()
        return

    # --- 3. Agrupar por similitud perceptual ---
    spinner3 = Spinner("Agrupando por similitud perceptual...")
    spinner3.start()
    grupos = agrupar_por_phash(candidatos, umbral)
    spinner3.stop(f"{len(grupos):,} grupos de imágenes similares")

    if incluir_raw_jpg:
        grupos_rj = agrupar_raw_jpg(candidatos)
        log_step(f"   + {len(grupos_rj):,} grupos RAW+JPG del mismo disparo")
        grupos.extend(grupos_rj)

    # --- 4. Elegir qué conservar y escribir el reporte ---
    filas = []
    total_descartables = 0
    bytes_recuperables = 0

    for grupo in grupos:
        ordenado = sorted(grupo, key=lambda c: c.puntaje(), reverse=True)
        mejor, resto = ordenado[0], ordenado[1:]
        # Si ni la mejor copia está en el disco, no hay nada que descartar:
        # proponerlo dejaría la foto sin ninguna copia en pie.
        if not mejor.existe:
            continue
        tipo = clasificar(grupo)
        for c in resto:
            if not c.existe:
                continue
            # En rafagas de deporte (misma cancha/luz/encuadre, jugador que
            # casi no se mueve entre frames) el pHash confunde tomas
            # DISTINTAS con recompresiones. Si el nombre de camara no
            # coincide, no es un duplicado de verdad: no vale la pena ni
            # reportarlo (ver conversacion con el usuario, redujo el balde
            # SIMILAR de 12.119 a 7.227 pendientes de un saque).
            if tipo == "SIMILAR" and (es_deporte(mejor.path) or es_deporte(c.path)):
                if _numero_secuencia(mejor.path) != _numero_secuencia(c.path):
                    continue
            total_descartables += 1
            bytes_recuperables += c.size
            filas.append({
                "tipo": tipo,
                "conservar": mejor.path,
                "descartar": c.path,
                "razon": _razon(mejor, c),
                "mb_descartado": round(c.size / (1024 * 1024), 2),
                "phash_conservar": mejor.phash,
                "phash_descartar": c.phash,
            })

    # Los exactos van al final: son los más seguros de borrar
    for sha, items in grupos_exactos.items():
        # Mismo orden de prioridades que puntaje(): primero que exista, después
        # que no esté dañado, y recién ahí la ruta. Bytes idénticos no quiere
        # decir que las dos copias sigan sanas en el disco de hoy.
        vivos_ex = [(p, d, os.path.exists(p)) for _, p, d in items]
        mejor, _, hay = min(vivos_ex,
                            key=lambda t: (not t[2], bool(t[1]),
                                           penalizacion_ruta(t[0]), len(t[0])))
        if not hay:
            continue
        for p, _d, existe in vivos_ex:
            if p == mejor or not existe:
                continue
            try:
                size = Path(p).stat().st_size
            except OSError:
                size = 0
            total_descartables += 1
            bytes_recuperables += size
            filas.append({
                "tipo": "EXACTO",
                "conservar": mejor,
                "descartar": p,
                "razon": f"SHA256 idéntico ({sha[:12]})",
                "mb_descartado": round(size / (1024 * 1024), 2),
                "phash_conservar": "",
                "phash_descartar": "",
            })

    with open(salida_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["tipo", "conservar", "descartar", "razon",
                                          "mb_descartado", "phash_conservar",
                                          "phash_descartar"])
        w.writeheader()
        w.writerows(filas)

    por_tipo = defaultdict(int)
    for fila in filas:
        por_tipo[fila["tipo"]] += 1

    log_step("=" * 60)
    log_step(f"📋 Reporte: {salida_csv}")
    for tipo, n in sorted(por_tipo.items(), key=lambda x: -x[1]):
        log_step(f"   {tipo:<14} {n:>7,} archivos")
    log_step(f"   {'TOTAL':<14} {total_descartables:>7,} archivos"
             f" = {bytes_recuperables / (1024**3):.1f} GB recuperables")
    log_step("=" * 60)

    log_step("Revisá el CSV, o mejor: abrí el curador (web_curador.py) en")
    log_step("/duplicados, marcá los que van y apretá 'Mover a cuarentena'.")
    conn.close()


def _razon(mejor: Candidato, descarte: Candidato) -> str:
    if es_raw(mejor.path) and not es_raw(descarte.path):
        return "el otro es RAW (más información)"
    if mejor.pixeles > descarte.pixeles > 0:
        return f"el otro tiene más resolución ({mejor.dims[0]}x{mejor.dims[1]} vs {descarte.dims[0]}x{descarte.dims[1]})"
    if mejor.camera_model and not descarte.camera_model:
        return "esta copia perdió el EXIF de cámara"
    if penalizacion_ruta(descarte.path) > penalizacion_ruta(mejor.path):
        return "está en una carpeta de copias (WhatsApp/descargas/etc.)"
    if mejor.size > descarte.size:
        return f"el otro pesa más ({mejor.size // 1024} KB vs {descarte.size // 1024} KB)"
    return "copia equivalente"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Detector minucioso de duplicados")
    ap.add_argument("-c", "--config", default="config.json")
    ap.add_argument("--umbral", type=int, default=5,
                    help="Distancia pHash máxima (0=idéntico, 5=default, 7=máx confiable)")
    ap.add_argument("--incluir-raw-jpg", action="store_true",
                    help="Tratar RAW+JPG del mismo disparo como duplicado")
    ap.add_argument("--csv", default="duplicados.csv")
    args = ap.parse_args()

    run(config_path=args.config, umbral=args.umbral,
        incluir_raw_jpg=args.incluir_raw_jpg, salida_csv=args.csv)
