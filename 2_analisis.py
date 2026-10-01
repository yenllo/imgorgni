import json
from pathlib import Path
from utils_tags import TagManager
from typing import List, Optional, Tuple, Dict
import numpy as np
import cv2
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import signal
import sys
from utils_log import Spinner, log_step

from db import (get_conn, init_db, fetch_files_needing_analysis, upsert_features_nocommit,
                set_needs_analysis_nocommit, enable_fast_writes, upsert_extras_nocommit,
                set_gps_nocommit, insert_faces_nocommit)
from utils_images import load_image_array
from utils_analisis import (SceneClassifier, PlaceResolver, FaceAnalyzer,
                            read_gps, quality_metrics, phash)

COCO_CLASSES = [
    "person","bicycle","car","motorcycle","airplane","bus","train","truck","boat","traffic light",
    "fire hydrant","stop sign","parking meter","bench","bird","cat","dog","horse","sheep","cow",
    "elephant","bear","zebra","giraffe","backpack","umbrella","handbag","tie","suitcase","frisbee",
    "skis","snowboard","sports ball","kite","baseball bat","baseball glove","skateboard","surfboard","tennis racket",
    "bottle","wine glass","cup","fork","knife","spoon","bowl","banana","apple","sandwich",
    "orange","broccoli","carrot","hot dog","pizza","donut","cake","chair","couch","potted plant",
    "bed","dining table","toilet","tv","laptop","mouse","remote","keyboard","cell phone","microwave",
    "oven","toaster","sink","refrigerator","book","clock","vase","scissors","teddy bear","hair drier","toothbrush"
]

# Variable global para manejar interrupciÃ³n
_interrupted = False

def _signal_handler(sig, frame):
    global _interrupted
    _interrupted = True
    log_step("\nâš ï¸ InterrupciÃ³n detectada. Guardando progreso...")

# Registrar handler
signal.signal(signal.SIGINT, _signal_handler)


def _try_import_ort():
    try:
        import onnxruntime as ort
        return ort
    except Exception:
        return None


def _providers(preferred: str, ort_mod):
    try:
        avail = ort_mod.get_available_providers()
    except Exception:
        avail = []
    
    pref = (preferred or "AUTO").upper()
    if pref == "DML":
        order = ["DmlExecutionProvider", "CUDAExecutionProvider"]
    else:  # CUDA o AUTO: preferir CUDA (nube), luego DML (Windows)
        order = ["CUDAExecutionProvider", "DmlExecutionProvider"]
    return [p for p in order if p in avail] + ["CPUExecutionProvider"]


def load_session(model_path: str, preferred: str = "AUTO", intra_threads: int = 4, inter_threads: int = 2) -> Optional[object]:
    ort = _try_import_ort()
    if ort is None:
        log_step("âš ï¸ ONNXRuntime no disponible.")
        return None
    
    try:
        so = ort.SessionOptions()
        try:
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        except Exception:
            pass
        try:
            so.intra_op_num_threads = int(intra_threads)
            so.inter_op_num_threads = int(inter_threads)
        except Exception:
            pass
        try:
            so.enable_mem_pattern = True
            so.enable_cpu_mem_arena = True
            so.enable_mem_reuse = True
        except Exception:
            pass
        
        provs = _providers(preferred, ort)
        sess = ort.InferenceSession(model_path, providers=provs, sess_options=so)
        
        log_step(f"âœ… Modelo: {Path(model_path).name}")
        log_step(f"   Providers: {sess.get_providers()}")
        return sess
    except Exception as e:
        log_step(f"âŒ Error: {e}")
        return None


def preprocess_yolo_batch(images: List[np.ndarray], input_size: int, 
                          preallocated: Optional[np.ndarray] = None) -> np.ndarray:
    batch_len = len(images)
    
    if preallocated is not None and preallocated.shape[0] >= batch_len:
        batch = preallocated[:batch_len]
        batch.fill(0)
    else:
        batch = np.zeros((batch_len, 3, input_size, input_size), dtype=np.float32)
    
    for i, img in enumerate(images):
        h, w = img.shape[:2]
        scale = input_size / max(h, w)
        new_w, new_h = int(w * scale), int(h * scale)
        
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        norm = resized.astype(np.float32) / 255.0
        
        y_offset = (input_size - new_h) // 2
        x_offset = (input_size - new_w) // 2
        
        for c in range(3):
            batch[i, c, y_offset:y_offset+new_h, x_offset:x_offset+new_w] = norm[:, :, c]
    
    return batch


def postprocess_yolo_batch(output: List[np.ndarray], batch_size: int, conf_th: float = 0.25) -> List[List[str]]:
    results = [[] for _ in range(batch_size)]
    
    if not output:
        return results
    
    arr = output[0]
    
    if arr.ndim == 3:
        for i in range(min(batch_size, arr.shape[0])):
            tags = set()
            dets = arr[i]
            
            if dets.shape[-1] >= 6:
                confs = dets[:, 4]
                valid_mask = confs >= conf_th
                valid_dets = dets[valid_mask]
                
                for det in valid_dets:
                    cls_scores = det[5:]
                    ci = int(np.argmax(cls_scores))
                    
                    if 0 <= ci < len(COCO_CLASSES):
                        tags.add(COCO_CLASSES[ci])
            
            results[i] = sorted(list(tags))
    
    return results


# Normalización por familia de modelo. SigLIP usa 0.5/0.5, CLIP los valores de
# OpenAI. Usar los de CLIP con un modelo SigLIP produce embeddings degradados
# sin ningún error visible, así que conviene leerlo del modelo cuando se puede.
NORM_CLIP = (np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32),
             np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32))
NORM_SIGLIP = (np.array([0.5, 0.5, 0.5], dtype=np.float32),
               np.array([0.5, 0.5, 0.5], dtype=np.float32))

_norm_actual = NORM_CLIP


def configurar_preproceso(model_path: str):
    """
    Ajusta la normalización según el modelo. Lee preprocess_cfg.json si está
    (los repos de immich lo incluyen); si no, deduce por el nombre.
    """
    global _norm_actual
    cfg_path = Path(model_path).parent / "preprocess_cfg.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            mean = np.array(cfg["mean"], dtype=np.float32)
            std = np.array(cfg["std"], dtype=np.float32)
            _norm_actual = (mean, std)
            log_step(f"   normalización desde preprocess_cfg.json: mean={mean[0]:.3f}")
            return
        except Exception:
            pass
    if "siglip" in str(model_path).lower():
        _norm_actual = NORM_SIGLIP
        log_step("   normalización SigLIP (0.5/0.5) deducida del nombre")
    else:
        _norm_actual = NORM_CLIP


def preprocess_feature_batch(images: List[np.ndarray], size: int = 224,
                            preallocated: Optional[np.ndarray] = None) -> np.ndarray:
    batch_len = len(images)
    mean, std = _norm_actual

    if (preallocated is not None and preallocated.shape[0] >= batch_len
            and preallocated.shape[2] == size):
        batch = preallocated[:batch_len]
    else:
        batch = np.zeros((batch_len, 3, size, size), dtype=np.float32)

    for i, img in enumerate(images):
        x = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA).astype(np.float32)
        x /= 255.0
        x = (x - mean) / std
        batch[i] = np.transpose(x, (2, 0, 1))

    return batch


def _build_feature_inputs(feat_sess, x: np.ndarray) -> Optional[Dict[str, np.ndarray]]:
    if feat_sess is None:
        return None
    
    try:
        names = [inp.name for inp in feat_sess.get_inputs()]
    except Exception:
        return None
    
    if not names:
        return None
    
    return {names[0]: x}


def fallback_hsv_embedding(img: np.ndarray, bins: int = 32) -> List[float]:
    try:
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        h = cv2.calcHist([hsv], [0], None, [bins], [0, 180]).flatten()
        s = cv2.calcHist([hsv], [1], None, [bins], [0, 256]).flatten()
        v = cv2.calcHist([hsv], [2], None, [bins], [0, 256]).flatten()
        feat = np.concatenate([h, s, v])
        norm = float(np.linalg.norm(feat))
        if norm > 1e-8:
            feat = feat / norm
        return feat.astype(float).tolist()
    except Exception:
        return [0.0] * (bins * 3)


def load_images_parallel(paths: List[tuple], max_workers: int = 8) -> List[tuple]:
    results = [None] * len(paths)
    
    def load_single(idx: int, pid: int, fpath: str):
        try:
            img = load_image_array(fpath)
            return (idx, pid, img, None)
        except Exception as e:
            return (idx, pid, None, str(e))
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(load_single, i, pid, fp): i 
            for i, (pid, fp) in enumerate(paths)
        }
        
        for future in as_completed(futures):
            idx, pid, img, err = future.result()
            results[idx] = (pid, img, err)
    
    return results


def run(config_path: str = "config.json", force: bool = False, tags_only: bool = False):
    global _interrupted
    
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    
    db_path = cfg["db_path"]
    yolo_path = cfg.get("yolo_onnx_path")
    feat_path = cfg.get("feature_model_onnx_path")
    yolo_size = int(cfg.get("yolo_input_size", 640))
    enable_yolo = bool(cfg.get("enable_yolo", True))
    enable_features = bool(cfg.get("enable_features", True))
    preferred_provider = str(cfg.get("preferred_provider", "AUTO"))
    ort_intra_threads = int(cfg.get("ort_intra_threads", 4))
    ort_inter_threads = int(cfg.get("ort_inter_threads", 2))
    batch_size = int(cfg.get("image_batch_size", 32))
    # CAMBIO: Commits mÃ¡s frecuentes (100 en lugar de 2000)
    commit_batch_size = int(cfg.get("commit_batch_size", 100))
    parallel_workers = int(cfg.get("parallel_loading_workers", 8))
    use_real_batching = bool(cfg.get("use_real_batching", True))
    
    # Análisis extendido (todo opcional, degrada solo)
    enable_scenes = bool(cfg.get("enable_scenes", True))
    enable_quality = bool(cfg.get("enable_quality", True))
    enable_phash = bool(cfg.get("enable_phash", True))
    enable_gps = bool(cfg.get("enable_gps", True))
    enable_faces = bool(cfg.get("enable_faces", False))
    scene_emb_path = cfg.get("scene_embeddings_path", "scene_embeddings.npz")

    conn = get_conn(db_path)
    init_db(conn)
    cache_mb = cfg.get("sqlite_cache_mb", 1024)
    enable_fast_writes(conn, cache_mb)

    tag_manager = TagManager(conn)
    log_step("âœ… TagManager inicializado")

    scene_clf = None
    if enable_scenes and Path(scene_emb_path).exists():
        try:
            scene_clf = SceneClassifier(scene_emb_path)
            log_step(f"✅ Escenas CLIP: {len(scene_clf.labels)} categorías ({scene_clf.dim} dims)")
        except Exception as e:
            log_step(f"⚠️ Escenas deshabilitadas: {e}")
    elif enable_scenes:
        log_step(f"⚠️ Sin {scene_emb_path} — corré: python precompute_scene_embeddings.py")

    place_resolver = PlaceResolver() if enable_gps else None

    face_analyzer = None
    if enable_faces:
        try:
            face_analyzer = FaceAnalyzer(use_gpu=(preferred_provider.upper() != "CPU"))
            log_step("✅ Detector de caras (InsightFace buffalo_l) cargado")
        except Exception as e:
            log_step(f"⚠️ Caras deshabilitadas: {e}")

    log_step("=" * 60)
    log_step("FASE 2: ANÃLISIS IA (EXTREMO v3.2)")
    if force:
        log_step("âš ï¸ MODO FORCE: Re-analizando TODAS las fotos")
    log_step("=" * 60)

    spinner = Spinner("Cargando modelos ONNX...")
    spinner.start()
    
    yolo_sess = None
    feat_sess = None
    
    if enable_yolo and yolo_path and Path(yolo_path).exists():
        yolo_sess = load_session(yolo_path, preferred_provider, ort_intra_threads, ort_inter_threads)
    else:
        log_step("âš ï¸ YOLO deshabilitado")
    
    if enable_features and feat_path and Path(feat_path).exists():
        feat_sess = load_session(feat_path, preferred_provider, ort_intra_threads, ort_inter_threads)
        configurar_preproceso(feat_path)
    else:
        log_step("âš ï¸ Features deshabilitado")
    
    spinner.stop("Modelos cargados")

    rows = fetch_files_needing_analysis(conn)

    # Las fotos que no salieron de una cámara dedicada no se organizan (van a una
    # carpeta aparte en el export). Analizarlas es gasto puro: acá se pueden
    # saltar y marcar como listas. Con ~50% de fotos de teléfono, ahorra la mitad.
    if cfg.get("analizar_solo_camara", False):
        from utils_analisis import clasificar_dispositivo
        cam = {r[0]: r[1] for r in conn.execute(
            "SELECT id, camera_model FROM files WHERE needs_analysis=1")}
        camara, otras = [], []
        for pid, fp in rows:
            if clasificar_dispositivo(cam.get(pid), fp) == "camara":
                camara.append((pid, fp))
            else:
                otras.append(pid)
        if otras:
            conn.executemany("UPDATE files SET needs_analysis=0 WHERE id=?",
                             [(p,) for p in otras])
            conn.commit()
            log_step(f"📱 {len(otras):,} fotos no-cámara salteadas (van sin organizar)")
        rows = camara
    
    def classify_file_type(row):
        _, file_path = row
        ext = Path(file_path).suffix.lower()
        if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
            return 0
        elif ext in {".tif", ".tiff"}:
            return 1
        return 2
    
    rows.sort(key=classify_file_type)
    
    type_counts = {0: 0, 1: 0, 2: 0}
    for row in rows:
        type_counts[classify_file_type(row)] += 1
    
    log_step(f"ðŸ“Š DistribuciÃ³n:")
    log_step(f"   âš¡ JPG/PNG: {type_counts[0]:,}")
    log_step(f"   ðŸ”¶ TIFF: {type_counts[1]:,}")
    log_step(f"   ðŸ¢ RAW: {type_counts[2]:,}")
    
    total = len(rows)
    if total == 0:
        log_step("âœ… No hay fotos pendientes")
        tag_manager.close()
        conn.close()
        return
    
    log_step(f"ðŸ“Š Total: {total:,}")
    log_step(f"ðŸ”¥ Batch: {batch_size} (inferencia REAL en batch)")
    log_step(f"ðŸš€ Workers: {parallel_workers} (carga paralela)")
    log_step(f"ðŸ’¾ SQLite cache: {cache_mb}MB")
    log_step(f"ðŸ§µ ONNX threads: {ort_intra_threads}+{ort_inter_threads}")
    log_step(f"ðŸ’¾ Commit cada: {commit_batch_size} fotos")
    
    stats = {'success': 0, 'errors': 0, 'yolo_tags': 0, 'embeddings': 0, 'fallback': 0,
             'scenes': 0, 'faces': 0, 'gps': 0}
    path_by_id = {pid: fp for pid, fp in rows}  # para leer GPS sin re-consultar la BD
    pbar = tqdm(total=total, desc="Fase 2", unit="foto")
    commit_counter = 0
    
    yolo_batch_prealloc = np.zeros((batch_size, 3, yolo_size, yolo_size), dtype=np.float32)
    # El lado de entrada lo dicta el modelo: CLIP usa 224, SigLIP-384 usa 384
    feat_size = 224
    if feat_sess is not None:
        try:
            forma = feat_sess.get_inputs()[0].shape
            if isinstance(forma[2], int) and forma[2] > 0:
                feat_size = int(forma[2])
        except Exception:
            pass
        log_step(f"🖼️ Entrada del modelo de features: {feat_size}px")

    feat_batch_prealloc = np.zeros((batch_size, 3, feat_size, feat_size), dtype=np.float32)
    
    log_step("âœ… Arrays pre-alocados en RAM")
    
    for batch_start in range(0, total, batch_size):
        # Verificar interrupciÃ³n
        if _interrupted:
            log_step("âš ï¸ Guardando progreso antes de salir...")
            if commit_counter > 0:
                conn.commit()
            break
            
        batch_end = min(batch_start + batch_size, total)
        batch_rows = rows[batch_start:batch_end]
        
        loaded = load_images_parallel(batch_rows, parallel_workers)
        
        images_ok = []
        photo_ids_ok = []
        
        for pid, img, err in loaded:
            if img is not None:
                images_ok.append(img)
                photo_ids_ok.append(pid)
            else:
                stats['errors'] += 1
                try:
                    upsert_features_nocommit(conn, pid, [], [])
                    set_needs_analysis_nocommit(conn, pid, 0)
                    commit_counter += 1
                except Exception:
                    pass
        
        if not images_ok:
            pbar.update(len(batch_rows))
            continue
        
        actual = len(images_ok)
        
        # YOLO
        all_tags = [[] for _ in range(actual)]
        if yolo_sess and use_real_batching:
            try:
                batch_input = preprocess_yolo_batch(images_ok, yolo_size, yolo_batch_prealloc)
                inputs = {yolo_sess.get_inputs()[0].name: batch_input}
                outputs = yolo_sess.run(None, inputs)
                all_tags = postprocess_yolo_batch(outputs, actual)
                stats['yolo_tags'] += sum(1 for t in all_tags if t)
            except Exception as e:
                error_msg = str(e).lower()
                if 'expected: 1' in error_msg or 'batch' in error_msg or 'dimension' in error_msg:
                    log_step("âš ï¸ Modelo YOLO no soporta batch. Procesando imagen por imagen...")
                    for i, img in enumerate(images_ok):
                        try:
                            single = preprocess_yolo_batch([img], yolo_size)
                            inputs = {yolo_sess.get_inputs()[0].name: single}
                            out = yolo_sess.run(None, inputs)
                            all_tags[i] = postprocess_yolo_batch(out, 1)[0]
                            if all_tags[i]:
                                stats['yolo_tags'] += 1
                        except Exception:
                            pass
                else:
                    log_step(f"âŒ Error YOLO: {e}")
        
        # FEATURES
        all_embs = [None for _ in range(actual)]
        if feat_sess and use_real_batching:
            try:
                batch_input = preprocess_feature_batch(images_ok, feat_size, feat_batch_prealloc)
                inputs = _build_feature_inputs(feat_sess, batch_input)
                
                if inputs:
                    outputs = feat_sess.run(None, inputs)
                    emb_array = np.array(outputs[0])
                    
                    if emb_array.ndim >= 2:
                        for i in range(actual):
                            all_embs[i] = emb_array[i].squeeze().astype(float).tolist()
                            stats['embeddings'] += 1
                    else:
                        for i in range(actual):
                            all_embs[i] = fallback_hsv_embedding(images_ok[i])
                            stats['fallback'] += 1
                else:
                    for i in range(actual):
                        all_embs[i] = fallback_hsv_embedding(images_ok[i])
                        stats['fallback'] += 1
            except Exception as e:
                error_msg = str(e).lower()
                if 'expected: 1' in error_msg or 'batch' in error_msg or 'dimension' in error_msg:
                    log_step("âš ï¸ Modelo Features no soporta batch. Procesando imagen por imagen...")
                    for i, img in enumerate(images_ok):
                        try:
                            single = preprocess_feature_batch([img], feat_size)
                            inputs = _build_feature_inputs(feat_sess, single)
                            if inputs:
                                out = feat_sess.run(None, inputs)
                                emb = np.array(out[0]).squeeze()
                                norm = np.linalg.norm(emb)
                                if norm > 1e-6:
                                    emb = emb / norm
                                all_embs[i] = emb.tolist()
                                stats['embeddings'] += 1
                            else:
                                all_embs[i] = fallback_hsv_embedding(img)
                                stats['fallback'] += 1
                        except Exception:
                            all_embs[i] = fallback_hsv_embedding(img)
                            stats['fallback'] += 1
                else:
                    log_step(f"âŒ Error Features: {e}")
                    for i in range(actual):
                        all_embs[i] = fallback_hsv_embedding(images_ok[i])
                        stats['fallback'] += 1
        else:
            for i in range(actual):
                all_embs[i] = fallback_hsv_embedding(images_ok[i])
                stats['fallback'] += 1
        
        # ESCENAS (zero-shot sobre los embeddings que ya calculamos: costo ~cero)
        all_scenes = [[] for _ in range(actual)]
        if scene_clf is not None:
            try:
                all_scenes = scene_clf.classify_batch(all_embs)
                stats['scenes'] += sum(1 for s in all_scenes if s)
            except Exception as e:
                log_step(f"⚠️ Error en escenas: {e}")

        # CARAS
        all_faces = [[] for _ in range(actual)]
        if face_analyzer is not None:
            for i, img in enumerate(images_ok):
                all_faces[i] = face_analyzer.analyze(img)
                if all_faces[i]:
                    stats['faces'] += len(all_faces[i])

        # GUARDAR
        for i in range(actual):
            try:
                pid = photo_ids_ok[i]
                if all_tags[i]:
                    all_tags[i] = tag_manager.register_tags(all_tags[i])

                upsert_features_nocommit(conn, pid, all_tags[i], all_embs[i])

                q = quality_metrics(images_ok[i]) if enable_quality else None
                ph = phash(images_ok[i]) if enable_phash else None
                if q or ph or all_scenes[i]:
                    upsert_extras_nocommit(conn, pid, all_scenes[i], q, ph)

                if enable_gps:
                    fp = path_by_id.get(pid)
                    if fp:
                        lat, lon = read_gps(fp)
                        if lat is not None:
                            place = place_resolver.resolve(lat, lon) if place_resolver else None
                            set_gps_nocommit(conn, pid, lat, lon, place)
                            stats['gps'] += 1

                if all_faces[i]:
                    insert_faces_nocommit(conn, pid, all_faces[i])

                set_needs_analysis_nocommit(conn, pid, 0)
                stats['success'] += 1
                commit_counter += 1
            except Exception:
                stats['errors'] += 1
        
        # Commit mÃ¡s frecuente
        if commit_counter >= commit_batch_size:
            conn.commit()
            commit_counter = 0
        
        pbar.set_postfix({'OK': stats['success'], 'Tags': stats['yolo_tags'], 'Emb': stats['embeddings']})
        pbar.update(len(batch_rows))
    
    # Commit final
    if commit_counter > 0:
        conn.commit()
    
    pbar.close()

    log_step("=" * 60)
    if _interrupted:
        log_step("FASE 2 INTERRUMPIDA (progreso guardado)")
    else:
        log_step("FASE 2 COMPLETADA")
    log_step(f"âœ… OK: {stats['success']:,}")
    log_step(f"âŒ Err: {stats['errors']:,}")
    log_step(f"ðŸ·ï¸ Tags: {stats['yolo_tags']:,}")
    log_step(f"ðŸŽ¯ Emb: {stats['embeddings']:,}")
    log_step(f"ðŸ”„ Fallback: {stats['fallback']:,}")
    log_step(f"🎬 Escenas: {stats['scenes']:,}")
    log_step(f"👤 Caras: {stats['faces']:,}")
    log_step(f"📍 Con GPS: {stats['gps']:,}")
    log_step("=" * 60)
    
    tag_manager.close()
    conn.close()
    log_step("ConexiÃ³n BD cerrada.")
    
    if _interrupted:
        sys.exit(0)