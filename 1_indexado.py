import os
import json
from pathlib import Path
import cv2
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from tqdm import tqdm
from utils_log import Spinner, log_step

from db import get_conn, init_db, enable_fast_writes, fetch_all_hashes, fetch_all_paths, upsert_file_nocommit
from utils_images import is_image_file, compute_sha256, read_exif_info, detect_watermark, move_to_discard, mobile_or_screenshot, SUPPORTED_RAW_EXTS


def iter_files_fast(root_dir: str):
    """
    Iterador de archivos robusto a corrupción de disco.

    En discos con sectores dañados, stat()/is_file() puede lanzar OSError
    (WinError 1392). os.walk con onerror ignora carpetas ilegibles, y el
    try/except por archivo saltea archivos ilegibles sin frenar el escaneo.
    """
    def _ignorar(err):
        pass  # carpeta ilegible: seguir con las demás

    # os.walk ya separó archivos de carpetas (via scandir): NO se re-verifica con
    # isfile() porque en un disco con sectores dañados cada stat extra puede
    # colgarse segundos. Un archivo ilegible se detecta recién al leerlo (el
    # worker lo saltea con su try/except).
    for carpeta, _dirs, archivos in os.walk(root_dir, onerror=_ignorar):
        for nombre in archivos:
            yield os.path.join(carpeta, nombre)


def classify_ext(path: str) -> int:
    """
    ClasificaciÃ³n por extensiÃ³n para ordenamiento optimizado
    
    Prioridad de procesamiento:
    0: JPG/JPEG/PNG/GIF/WEBP (mÃ¡s rÃ¡pidos, ~95% de fotos tÃ­picas)
    1: TIFF/TIF (medios)
    2: RAW - CR2/ARW/NEF/etc. (mÃ¡s lentos, requieren rawpy)
    
    Returns:
        0, 1 o 2 segÃºn prioridad
    """
    ext = Path(path).suffix.lower()
    
    # Prioridad 0: Formatos rÃ¡pidos (procesamiento directo con PIL/OpenCV)
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        return 0
    
    # Prioridad 1: Formatos medios
    if ext in {".tif", ".tiff"}:
        return 1
    
    # Prioridad 2: RAW (mÃ¡s lentos)
    if ext in SUPPORTED_RAW_EXTS:
        return 2
    
    # Cualquier otro formato
    return 1


def get_file_size_safe(fp: str) -> int:
    """Obtiene tamaÃ±o de archivo de forma segura"""
    try:
        return os.path.getsize(fp)
    except Exception:
        return float('inf')


def index_directory(root_dir: str, watermark_path: str | None, discard_duplicates_dir: str,
                    discard_wm: str, discard_mobile: str, wm_threshold: float, force: bool = False,
                    config_path: str = "config.json", no_mover: bool = False):
    """
    Fase 1 optimizada: IndexaciÃ³n y filtrado con mejor rendimiento
    
    Args:
        force: Si es True, limpia BD y escanea TODO desde cero
        config_path: Ruta al archivo de configuraciÃ³n
    
    Mejoras:
    - Uso de rglob para bÃºsqueda recursiva mÃ¡s rÃ¡pida
    - Pre-carga de hashes en set (O(1) lookup)
    - Batch commits mÃ¡s grandes para reducir I/O
    - Ordenamiento inteligente (procesar rÃ¡pidos primero)
    - Skip de archivos ya indexados (reanudar)
    """
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    
    conn = get_conn(cfg["db_path"])
    init_db(conn)
    
    # Usar cache grande de SQLite (mÃ¡s RAM = menos I/O)
    cache_mb = cfg.get("sqlite_cache_mb", 512)
    enable_fast_writes(conn, cache_mb)

    enable_mobile_filter = cfg.get("enable_mobile_filter", False)

    log_step("Fase 1: Preparando BD y cargando Ã­ndices existentes")

    # Si force=True, limpiar TODA la base de datos
    if force:
        log_step("âš ï¸ MODO FORCE: Limpiando base de datos...")
        conn.execute("DELETE FROM event_photos")
        conn.execute("DELETE FROM events")
        conn.execute("DELETE FROM photo_features")
        conn.execute("DELETE FROM files")
        conn.commit()
        log_step("âœ… Base de datos limpiada. Escaneando TODO desde cero...")
        known_hashes = set()
        existing_paths = set()
    else:
        # Pre-cargar hashes y paths en memoria para lookup rÃ¡pido
        known_hashes = set(fetch_all_hashes(conn))
        existing_paths = set(fetch_all_paths(conn))
        log_step(f"Ãndices cargados: {len(known_hashes)} hashes, {len(existing_paths)} paths")

    # Cargar plantilla de watermark
    template_gray = None
    if watermark_path and Path(watermark_path).exists():
        try:
            template_gray = cv2.imread(watermark_path, cv2.IMREAD_GRAYSCALE)
            if template_gray is not None:
                log_step(f"Plantilla de watermark cargada: {template_gray.shape}")
        except Exception as e:
            log_step(f"Advertencia: No se pudo cargar watermark: {e}")

    # Escaneo y ordenamiento optimizado
    spinner = Spinner("Escaneando directorio...")
    spinner.start()
    
    # Carpetas a excluir (ej: una descarga en curso que aún no está completa).
    # Los directorios de descarte SIEMPRE se excluyen del propio escaneo: viven
    # dentro de root_dir, y si no se excluyen el pipeline se los vuelve a topar
    # en la próxima corrida, los redetecta como duplicados/watermark/etc, y
    # move_to_discard los anida un nivel más adentro de sí mismos cada vez.
    exclude = [e.lower() for e in cfg.get("exclude_dirs", [])]
    exclude += [d.lower() for d in (discard_duplicates_dir, discard_wm, discard_mobile) if d]
    def _excluida(fp: str) -> bool:
        low = fp.lower()
        return any(e in low for e in exclude)

    all_files = [fp for fp in iter_files_fast(root_dir)
                 if is_image_file(fp) and not _excluida(fp)]
    if exclude:
        log_step(f"Excluyendo carpetas: {', '.join(exclude)}")
    
    # OPTIMIZACIÃ“N CLAVE: Ordenar por tipo de archivo
    # 1. Primero los rÃ¡pidos (JPG, PNG, etc.)
    # 2. Luego medios (TIFF)
    # 3. Al final los lentos (RAW)
    # Dentro de cada grupo, ordenar por tamaÃ±o (pequeÃ±os primero)
    # Ordenar SOLO por tipo (extensión = string, sin tocar el disco). Antes se
    # sub-ordenaba por tamaño con get_file_size_safe(), lo que hacía una llamada
    # stat por archivo: 90k stats sobre un disco lento/dañado = escaneo eterno.
    all_files.sort(key=classify_ext)
    
    spinner.stop(f"Encontrados {len(all_files)} archivos de imagen")
    
    # Mostrar distribuciÃ³n por tipo
    by_type = {0: 0, 1: 0, 2: 0}
    for fp in all_files:
        by_type[classify_ext(fp)] += 1
    
    log_step(f"ðŸ“Š DistribuciÃ³n:")
    log_step(f"   âš¡ JPG/PNG/GIF/WEBP: {by_type[0]:,} (rÃ¡pidos)")
    log_step(f"   ðŸ”¶ TIFF: {by_type[1]:,} (medios)")
    log_step(f"   ðŸ¢ RAW: {by_type[2]:,} (lentos)")

    # Filtrar archivos ya indexados
    files_to_process = [fp for fp in all_files if fp not in existing_paths]
    log_step(f"Archivos nuevos a procesar: {len(files_to_process)}")

    if not files_to_process:
        log_step("No hay archivos nuevos. Fase 1 completada.")
        conn.close()
        return

    pbar = tqdm(total=len(files_to_process), desc="Fase 1: Indexando")

    lock = threading.Lock()
    stats = {'duplicates': 0, 'watermark': 0, 'mobile': 0, 'indexed': 0, 'fast': 0, 'medium': 0, 'raw': 0}

    def worker(fp: str):
        """Worker thread optimizado"""
        try:
            # Clasificar tipo de archivo para estadÃ­sticas
            file_type = classify_ext(fp)
            
            # Calcular hash primero (mÃ¡s rÃ¡pido que cargar imagen)
            sha = compute_sha256(fp)
            
            # Check duplicado por contenido
            with lock:
                if sha in known_hashes:
                    stats['duplicates'] += 1
                    if no_mover:
                        # Se indexa igual: si no se mueve NI se indexa, el
                        # archivo queda invisible para el sistema y cada
                        # corrida lo vuelve a descubrir. Indexado, la Fase 5
                        # y el curador lo tratan como el duplicado que es.
                        pass
                    else:
                        try:
                            move_to_discard(root_dir, fp, discard_duplicates_dir)
                        except Exception:
                            pass
                        return None
            
            # Watermark check (solo en JPG/PNG/GIF/WEBP - formatos rÃ¡pidos)
            ext = Path(fp).suffix.lower()
            if template_gray is not None and ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                try:
                    if detect_watermark(fp, template_gray, wm_threshold):
                        stats['watermark'] += 1
                        if not no_mover:
                            move_to_discard(root_dir, fp, discard_wm)
                            return None
                except Exception:
                    pass
            
            # Leer EXIF
            capture_date, camera_model = read_exif_info(fp)
            
            # Filtro mÃ³vil/screenshot (opcional)
            if enable_mobile_filter:
                if mobile_or_screenshot(fp, camera_model):
                    stats['mobile'] += 1
                    if not no_mover:
                        try:
                            move_to_discard(root_dir, fp, discard_mobile)
                            return None
                        except Exception:
                            pass
            
            # Actualizar estadÃ­sticas por tipo
            with lock:
                if file_type == 0:
                    stats['fast'] += 1
                elif file_type == 1:
                    stats['medium'] += 1
                elif file_type == 2:
                    stats['raw'] += 1
            
            # Archivo vÃ¡lido para indexar
            return (fp, capture_date, camera_model, sha)
            
        except Exception as e:
            # Log silencioso de errores para no interrumpir el flujo
            return None

    # Procesamiento paralelo optimizado: menos CPU, mÃ¡s RAM
    max_workers = cfg.get("max_workers_phase1", 16)  # Reducido para menos CPU
    cur = conn.cursor()
    batch = []
    batch_size = cfg.get("commit_batch_size", 1000)  # Batches mÃ¡s grandes = menos I/O
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(worker, fp): fp for fp in files_to_process}
        
        for future in as_completed(futures):
            result = future.result()
            
            if result is not None:
                fp, capture_date, camera_model, sha = result
                
                # Usar upsert sin commit
                upsert_file_nocommit(cur, fp, capture_date, camera_model, sha, needs_analysis=1)
                
                with lock:
                    known_hashes.add(sha)
                    existing_paths.add(fp)
                    stats['indexed'] += 1
                    batch.append(result)
                
                # Commit en lotes para reducir I/O
                if len(batch) >= batch_size:
                    conn.commit()
                    batch.clear()
            
            pbar.update(1)
    
    # Commit final
    if batch:
        conn.commit()

    pbar.close()
    
    # Reporte final
    log_step("=" * 60)
    log_step("FASE 1 COMPLETADA")
    log_step(f"âœ… Indexadas: {stats['indexed']}")
    log_step(f"   âš¡ JPG/PNG/GIF/WEBP: {stats['fast']}")
    log_step(f"   ðŸ”¶ TIFF: {stats['medium']}")
    log_step(f"   ðŸ¢ RAW: {stats['raw']}")
    log_step(f"ðŸ—‘ï¸ Duplicados descartados: {stats['duplicates']}")
    log_step(f"ðŸ’§ Watermark descartadas: {stats['watermark']}")
    log_step(f"ðŸ“± MÃ³vil/Screenshot descartadas: {stats['mobile']}")
    log_step("=" * 60)
    
    conn.close()


def run(config_path: str = "config.json", force: bool = False, no_mover: bool = False):
    """Punto de entrada principal

    Args:
        config_path: Ruta al archivo de configuraciÃ³n
        force: Si es True, escanea todo desde cero (ignora progreso)
        no_mover: Si es True, NO mueve nada al disco: los duplicados exactos,
            las marcas de agua y las capturas se indexan igual y quedan para
            que los resuelva la Fase 5 y el curador. Sirve para correr el
            pipeline sobre material nuevo sin reacomodar archivos.
    """
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    
    root_dir = cfg["root_dir"]
    watermark_path = cfg.get("watermark_path")
    discard_duplicates_dir = cfg.get("discard_duplicates_dir", "_duplicados_contenido")
    discard_wm = cfg.get("discard_watermark_dir", "_descartadas_watermark")
    discard_mobile = cfg.get("discard_mobile_dir", "_descartadas_movil_screenshot")
    wm_threshold = float(cfg.get("watermark_threshold", 0.85))
    
    if force:
        log_step("âš ï¸ MODO FORCE: IndexarÃ¡ TODAS las fotos desde cero")
    
    no_mover = no_mover or bool(cfg.get("no_mover", False))
    if no_mover:
        log_step("MODO NO-MOVER: no se mueve ningun archivo; los duplicados se indexan igual")

    index_directory(root_dir, watermark_path, discard_duplicates_dir, discard_wm,
                    discard_mobile, wm_threshold, force, config_path, no_mover)


if __name__ == "__main__":
    run()
