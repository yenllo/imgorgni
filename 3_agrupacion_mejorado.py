"""
Fase 3 Mejorada: Re-cÃ¡lculo inteligente de clusters
NO borra eventos verificados, solo recalcula clustering
"""
import json
from datetime import datetime
from collections import Counter
from typing import List, Tuple, Dict
import numpy as np
from sklearn.cluster import DBSCAN
from utils_log import Spinner, log_step
import time
import threading

from db import get_conn, init_db, fetch_features_with_time, create_event, assign_photos_to_event
from pathlib import Path


def _parse_iso(ts: str) -> float:
    """Convierte timestamp ISO a horas desde epoch"""
    dt = datetime.fromisoformat(ts)
    return dt.timestamp() / 3600.0


def make_samples(rows: List[dict]) -> Tuple[np.ndarray, List[int]]:
    """Prepara muestras para clustering con normalizaciÃ³n"""
    valid_samples = []
    valid_ids = []
    
    for r in rows:
        emb = np.array(r["embedding"], dtype=np.float32)
        if emb.size == 0:
            continue
        
        norm = float(np.linalg.norm(emb))
        if norm <= 1e-8:
            continue
        emb = emb / norm
        
        if not r.get("capture_date"):
            continue
        
        try:
            ts_hours = _parse_iso(r["capture_date"])
        except Exception:
            continue
        
        if np.isnan(ts_hours) or np.isinf(ts_hours):
            continue
        
        vec = np.concatenate([emb, np.array([ts_hours], dtype=np.float32)])
        valid_samples.append(vec)
        valid_ids.append(r["photo_id"])
    
    if not valid_samples:
        return np.empty((0, 1), dtype=np.float32), []
    
    return np.stack(valid_samples, axis=0), valid_ids


class DualMetric:
    """MÃ©trica dual para DBSCAN: combina similitud visual + temporal"""
    def __init__(self, time_window_hours: float, min_cosine: float):
        self.time_window_hours = float(time_window_hours)
        self.min_cosine = float(min_cosine)

    def __call__(self, a: np.ndarray, b: np.ndarray) -> float:
        ta, tb = float(a[-1]), float(b[-1])
        dt = abs(ta - tb)
        
        time_norm = dt / self.time_window_hours
        
        if time_norm > 1.0:
            return 2.0
        
        va = a[:-1]
        vb = b[:-1]
        
        denom = (np.linalg.norm(va) * np.linalg.norm(vb))
        cos = (float(np.dot(va, vb)) / denom) if denom > 0 else 0.0
        
        if cos < self.min_cosine:
            return 2.0
        
        return time_norm


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Calcula similitud coseno entre dos vectores"""
    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    return (float(np.dot(a, b)) / denom) if denom > 0 else 0.0


def fallback_greedy_clusters(rows: List[dict], time_window_hours: float, min_cosine: float) -> List[List[int]]:
    """Algoritmo greedy de clustering como fallback de DBSCAN"""
    valid = []
    for r in rows:
        emb = r.get("embedding")
        if not emb or not r.get("capture_date"):
            continue
        
        v = np.array(emb, dtype=np.float32)
        n = float(np.linalg.norm(v))
        if n <= 1e-8:
            continue
        
        try:
            ts_h = _parse_iso(r["capture_date"])
        except Exception:
            continue
        
        valid.append({
            'photo_id': r['photo_id'],
            'emb_np': v / n,
            'ts_h': ts_h,
            'tags': r.get('tags', [])
        })
    
    if not valid:
        return []
    
    valid.sort(key=lambda x: x['ts_h'])
    
    clusters = []
    current_cluster = []
    current_centroid = None
    last_ts = None
    
    for item in valid:
        v = item['emb_np']
        ts = item['ts_h']
        
        if not current_cluster:
            current_cluster = [item]
            current_centroid = v.copy()
            last_ts = ts
            continue
        
        dt = abs(ts - last_ts)
        cos = _cosine(v, current_centroid)
        
        if dt <= time_window_hours and cos >= min_cosine:
            current_cluster.append(item)
            
            all_embs = np.array([x['emb_np'] for x in current_cluster])
            current_centroid = np.mean(all_embs, axis=0)
            c_norm = float(np.linalg.norm(current_centroid))
            if c_norm > 1e-8:
                current_centroid = current_centroid / c_norm
            
            last_ts = ts
        else:
            if len(current_cluster) >= 5:
                clusters.append([x['photo_id'] for x in current_cluster])
            
            current_cluster = [item]
            current_centroid = v.copy()
            last_ts = ts
    
    if len(current_cluster) >= 5:
        clusters.append([x['photo_id'] for x in current_cluster])
    
    return clusters


def summarize_cluster(rows: List[dict]) -> Tuple[str, str, str]:
    """
    Genera metadata de un cluster.
    Prioridad para el nombre: escenas CLIP > objetos YOLO, y antepone el lugar
    del GPS si lo hay. Ej: "Viña del Mar - beach, sunset"
    """
    scenes, tags, times, places = [], [], [], []

    for r in rows:
        scenes.extend(r.get("scene_tags", []))
        tags.extend(r.get("tags", []))
        if r.get("place"):
            places.append(r["place"])
        if r.get("capture_date"):
            try:
                times.append(datetime.fromisoformat(r["capture_date"]))
            except Exception:
                pass

    # Las escenas describen mejor un evento que los objetos sueltos
    top = [s for s, _ in Counter(scenes).most_common(3)] if scenes else \
          [t for t, _ in Counter(tags).most_common(3)]

    suggested_name = ", ".join(top) if top else None
    if places:
        lugar = Counter(places).most_common(1)[0][0]
        suggested_name = f"{lugar} - {suggested_name}" if suggested_name else lugar

    if not times:
        now_iso = datetime.now().isoformat()
        return suggested_name or "Evento", now_iso, now_iso
    
    start = min(times).isoformat()
    end = max(times).isoformat()
    
    return suggested_name or "Evento", start, end


def estimate_dbscan_time(X: np.ndarray, metric, min_samples: int, sample_size: int = 1000) -> float:
    """Estima tiempo de ejecuciÃ³n de DBSCAN usando muestra"""
    try:
        n = X.shape[0]
        if n < sample_size:
            return 0.0
        
        idx = np.random.choice(n, size=sample_size, replace=False)
        X_sample = X[idx]
        
        dbs_sample = DBSCAN(eps=1.0, min_samples=min_samples, metric=metric, n_jobs=1)
        
        t0 = time.time()
        dbs_sample.fit(X_sample)
        t_elapsed = time.time() - t0
        
        scale_factor = (n / sample_size) ** 1.8
        estimated = t_elapsed * scale_factor
        
        return max(1.0, estimated)
    except Exception:
        return 0.0


def get_verified_events(conn) -> set:
    """Obtiene IDs de eventos que tienen nombre personalizado (verificados)"""
    try:
        cur = conn.execute("SELECT id FROM events WHERE name IS NOT NULL")
        return {row[0] for row in cur.fetchall()}
    except Exception:
        return set()


def preserve_verified_events(conn, verified_event_ids: set):
    """Preserva eventos verificados en una tabla temporal"""
    if not verified_event_ids:
        return
    
    try:
        # Crear tabla temporal
        conn.execute("DROP TABLE IF EXISTS temp_verified_events")
        conn.execute("""
            CREATE TEMP TABLE temp_verified_events AS
            SELECT * FROM events WHERE id IN ({})
        """.format(','.join(str(x) for x in verified_event_ids)))
        
        conn.execute("""
            CREATE TEMP TABLE temp_verified_event_photos AS
            SELECT * FROM event_photos WHERE event_id IN ({})
        """.format(','.join(str(x) for x in verified_event_ids)))
        
        log_step(f"âœ… {len(verified_event_ids)} eventos verificados preservados")
    except Exception as e:
        log_step(f"âš ï¸ Error preservando eventos: {e}")


def restore_verified_events(conn):
    """Restaura eventos verificados desde tabla temporal"""
    try:
        # Verificar si existen tablas temporales
        cur = conn.execute("SELECT name FROM sqlite_temp_master WHERE type='table' AND name='temp_verified_events'")
        if not cur.fetchone():
            return
        
        # Restaurar eventos
        conn.execute("""
            INSERT OR REPLACE INTO events (id, suggested_name, name, start_time, end_time)
            SELECT id, suggested_name, name, start_time, end_time FROM temp_verified_events
        """)
        
        # Restaurar relaciones foto-evento
        conn.execute("""
            INSERT OR REPLACE INTO event_photos (event_id, photo_id)
            SELECT event_id, photo_id FROM temp_verified_event_photos
        """)
        
        conn.commit()
        
        cur = conn.execute("SELECT COUNT(*) FROM temp_verified_events")
        count = cur.fetchone()[0]
        log_step(f"âœ… {count} eventos verificados restaurados")
        
        # Limpiar temporales
        conn.execute("DROP TABLE IF EXISTS temp_verified_events")
        conn.execute("DROP TABLE IF EXISTS temp_verified_event_photos")
        
    except Exception as e:
        log_step(f"âš ï¸ Error restaurando eventos: {e}")


def auto_merge_similar_events(conn, cfg: dict, id_to_row: dict):
    """Post-procesamiento: Fusiona automÃ¡ticamente eventos muy similares"""
    if not cfg.get("auto_merge_similar_events", False):
        return 0
    
    time_gap_hours = float(cfg.get("auto_merge_time_gap_hours", 12))
    sim_threshold = float(cfg.get("auto_merge_similarity_threshold", 0.80))
    
    log_step("="*60)
    log_step("POST-PROCESAMIENTO: FusiÃ³n automÃ¡tica de eventos similares")
    log_step(f"  - Gap temporal mÃ¡ximo: {time_gap_hours}h")
    log_step(f"  - Similitud mÃ­nima: {sim_threshold}")
    
    # Solo fusionar eventos auto-generados (sin nombre personalizado)
    cur = conn.execute("""
        SELECT e.id, e.suggested_name, e.start_time, e.end_time,
               GROUP_CONCAT(ep.photo_id) as photo_ids
        FROM events e
        LEFT JOIN event_photos ep ON e.id = ep.event_id
        WHERE e.name IS NULL
        GROUP BY e.id
        ORDER BY e.start_time
    """)
    
    events_data = []
    for event_id, name, start, end, photo_ids_str in cur.fetchall():
        if not photo_ids_str:
            continue
        
        photo_ids = [int(x) for x in photo_ids_str.split(',')]
        
        embeddings = []
        tags_all = []
        for pid in photo_ids:
            if pid in id_to_row:
                row = id_to_row[pid]
                emb = np.array(row.get('embedding', []), dtype=np.float32)
                if emb.size > 0:
                    norm = float(np.linalg.norm(emb))
                    if norm > 1e-8:
                        embeddings.append(emb / norm)
                tags_all.extend(row.get('tags', []))
        
        if not embeddings:
            continue
        
        centroid = np.mean(embeddings, axis=0)
        c_norm = float(np.linalg.norm(centroid))
        if c_norm > 1e-8:
            centroid = centroid / c_norm
        
        try:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
        except:
            continue
        
        events_data.append({
            'id': event_id,
            'name': name,
            'start_dt': start_dt,
            'end_dt': end_dt,
            'photo_ids': photo_ids,
            'centroid': centroid,
            'tags': tags_all
        })
    
    if len(events_data) < 2:
        log_step("  âš ï¸ Menos de 2 eventos auto-generados para fusionar")
        return 0
    
    merges = []
    
    for i in range(len(events_data)):
        for j in range(i+1, len(events_data)):
            e1 = events_data[i]
            e2 = events_data[j]
            
            time_gap = abs((e2['start_dt'] - e1['end_dt']).total_seconds() / 3600)
            if time_gap > time_gap_hours:
                continue
            
            cos_sim = float(np.dot(e1['centroid'], e2['centroid']))
            if cos_sim < sim_threshold:
                continue
            
            tags1 = set(e1['tags'])
            tags2 = set(e2['tags'])
            if tags1 and tags2:
                jaccard = len(tags1 & tags2) / len(tags1 | tags2)
                if jaccard < 0.3:
                    continue
            
            merges.append((e1['id'], e2['id'], cos_sim, time_gap))
    
    if not merges:
        log_step("  âœ… No se encontraron eventos para fusionar")
        return 0
    
    merges.sort(key=lambda x: x[2], reverse=True)
    
    log_step(f"  ðŸ“Š Encontrados {len(merges)} pares candidatos a fusiÃ³n")
    
    merged_count = 0
    already_merged = set()
    
    for e1_id, e2_id, similarity, gap in merges:
        if e1_id in already_merged or e2_id in already_merged:
            continue
        
        cur = conn.execute(
            "SELECT suggested_name, start_time, end_time FROM events WHERE id IN (?, ?)",
            (e1_id, e2_id)
        )
        infos = cur.fetchall()
        if len(infos) != 2:
            continue
        
        names = [info[0] for info in infos if info[0]]
        combined_name = names[0] if names else "Evento fusionado"
        
        all_times = [datetime.fromisoformat(t) for info in infos for t in [info[1], info[2]]]
        new_start = min(all_times).isoformat()
        new_end = max(all_times).isoformat()
        
        new_event_id = create_event(conn, combined_name, new_start, new_end)
        
        conn.execute("""
            INSERT OR IGNORE INTO event_photos (event_id, photo_id)
            SELECT ?, photo_id FROM event_photos WHERE event_id IN (?, ?)
        """, (new_event_id, e1_id, e2_id))
        
        conn.execute("DELETE FROM events WHERE id IN (?, ?)", (e1_id, e2_id))
        conn.execute("DELETE FROM event_photos WHERE event_id IN (?, ?)", (e1_id, e2_id))
        
        already_merged.add(e1_id)
        already_merged.add(e2_id)
        merged_count += 1
        
        log_step(f"  ðŸ”— Fusionados: {e1_id} + {e2_id} â†’ {new_event_id} (sim={similarity:.2f}, gap={gap:.1f}h)")
    
    conn.commit()
    log_step(f"  âœ… Total de fusiones: {merged_count}")
    log_step("="*60)
    
    return merged_count


def run(config_path: str = "config.json", force: bool = False):
    """
    Fase 3 mejorada: Re-cÃ¡lculo inteligente de clusters
    
    Args:
        config_path: Ruta al archivo de configuraciÃ³n
        force: Si es True, PRESERVA eventos verificados y solo recalcula auto-generados
    """
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    
    db_path = cfg["db_path"]
    time_window_hours = float(cfg.get("cluster_time_window_hours", 2))
    min_cos = float(cfg.get("cluster_cosine_threshold", 0.85))
    min_samples = int(cfg.get("dbscan_min_samples", 6))
    n_jobs = int(cfg.get("cluster_n_jobs", -1))

    conn = get_conn(db_path)
    init_db(conn)
    
    conn.execute("PRAGMA cache_size=-524288")
    conn.execute("PRAGMA mmap_size=1073741824")
    conn.execute("PRAGMA temp_store=MEMORY")

    log_step("=" * 60)
    log_step("FASE 3: AGRUPACIÃ“N DE EVENTOS (MODO INTELIGENTE)")
    if force:
        log_step("âš¡ MODO FORCE: Preservando eventos verificados")
        log_step("   Solo se recalcularÃ¡n eventos auto-generados")
    log_step("=" * 60)
    log_step(f"ParÃ¡metros:")
    log_step(f"  - Ventana temporal: {time_window_hours} horas")
    log_step(f"  - Umbral coseno: {min_cos}")
    log_step(f"  - Min. samples: {min_samples}")
    log_step(f"  - Workers: {n_jobs if n_jobs > 0 else 'auto'}")

    # NUEVO: Preservar eventos verificados
    verified_events = get_verified_events(conn)
    if verified_events:
        log_step(f"ðŸ“Œ Eventos verificados encontrados: {len(verified_events)}")
        preserve_verified_events(conn, verified_events)
    
    # Solo borrar eventos auto-generados
    if force:
        log_step("ðŸ—‘ï¸ Eliminando solo eventos auto-generados...")
        conn.execute("DELETE FROM event_photos WHERE event_id IN (SELECT id FROM events WHERE name IS NULL)")
        conn.execute("DELETE FROM events WHERE name IS NULL")
        conn.commit()
        log_step("âœ… Eventos auto-generados eliminados")

    spinner = Spinner("Cargando caracterÃ­sticas desde BD...")
    spinner.start()
    rows = fetch_features_with_time(conn)
    
    def classify_row_type(row):
        photo_id = row.get('photo_id')
        try:
            cur = conn.execute("SELECT file_path FROM files WHERE id=?", (photo_id,))
            result = cur.fetchone()
            if result:
                ext = Path(result[0]).suffix.lower()
                if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                    return 0
                elif ext in {".tif", ".tiff"}:
                    return 1
                elif ext in {".cr2", ".arw", ".nef", ".rw2", ".raf", ".raw", ".dng"}:
                    return 2
        except:
            pass
        return 1
    
    rows.sort(key=classify_row_type)
    spinner.stop(f"Datos cargados: {len(rows)} fotos (ordenadas por tipo)")

    if len(rows) == 0:
        log_step("âš ï¸ No hay datos para agrupar")
        restore_verified_events(conn)
        conn.close()
        return

    spinner2 = Spinner("Preparando muestras para clustering...")
    spinner2.start()
    X, ids = make_samples(rows)
    spinner2.stop(f"Muestras vÃ¡lidas: {X.shape[0]}")

    if X.shape[0] < min_samples:
        log_step(f"âš ï¸ Datos insuficientes para clustering (mÃ­nimo: {min_samples})")
        restore_verified_events(conn)
        conn.close()
        return

    metric = DualMetric(time_window_hours, min_cos)
    
    if n_jobs == -1:
        n_jobs = cfg.get("cluster_n_jobs", 4)
    
    est_seconds = estimate_dbscan_time(X, metric, min_samples, sample_size=min(2000, X.shape[0] // 5))
    if est_seconds > 0:
        log_step(f"â±ï¸ Tiempo estimado de clustering: ~{int(est_seconds)}s")

    dbs = DBSCAN(eps=1.0, min_samples=min_samples, metric=metric, n_jobs=n_jobs, algorithm='ball_tree')
    
    spinner3 = Spinner("Ejecutando DBSCAN...")
    spinner3.start()
    
    labels = None
    error_flag = [None]
    done_flag = [False]

    def dbscan_worker():
        nonlocal labels, done_flag
        try:
            labels = dbs.fit_predict(X)
        except Exception as e:
            error_flag[0] = e
        finally:
            done_flag[0] = True

    worker_thread = threading.Thread(target=dbscan_worker, daemon=True)
    start_time = time.time()
    worker_thread.start()

    while not done_flag[0]:
        elapsed = time.time() - start_time
        if est_seconds and est_seconds > 0:
            pct = min(99.0, (elapsed / est_seconds) * 100.0)
            spinner3.text = f"Ejecutando DBSCAN... {int(elapsed)}s ({int(pct)}%)"
        else:
            spinner3.text = f"Ejecutando DBSCAN... {int(elapsed)}s"
        time.sleep(0.5)

    worker_thread.join()
    elapsed_total = time.time() - start_time
    spinner3.stop(f"DBSCAN completado en {int(elapsed_total)}s")

    if error_flag[0]:
        log_step(f"âŒ Error en DBSCAN: {error_flag[0]}")
        log_step("ðŸ”„ Usando algoritmo greedy como fallback...")
        
        id_to_row = {r["photo_id"]: r for r in rows}
        greedy_clusters = fallback_greedy_clusters(rows, time_window_hours, min_cos)
        
        if not greedy_clusters:
            log_step("âŒ No se pudieron generar clusters. Ajusta parÃ¡metros en config.json")
            restore_verified_events(conn)
            conn.close()
            return
        
        created = 0
        for photo_ids in greedy_clusters:
            cluster_rows = [id_to_row[pid] for pid in photo_ids if pid in id_to_row]
            name, start, end = summarize_cluster(cluster_rows)
            ev_id = create_event(conn, name, start, end)
            assign_photos_to_event(conn, ev_id, photo_ids)
            created += 1
        
        restore_verified_events(conn)
        
        log_step("=" * 60)
        log_step(f"âœ… FASE 3 COMPLETADA (Fallback Greedy)")
        log_step(f"ðŸ“ Eventos creados: {created}")
        if verified_events:
            log_step(f"ðŸ“Œ Eventos verificados preservados: {len(verified_events)}")
        log_step("=" * 60)
        conn.close()
        return

    clusters: Dict[int, List[int]] = {}
    noise_count = 0
    
    for photo_id, label in zip(ids, labels):
        if label == -1:
            noise_count += 1
            continue
        clusters.setdefault(int(label), []).append(int(photo_id))

    log_step(f"ðŸ“Š Clusters detectados: {len(clusters)}")
    log_step(f"ðŸ“‡ Fotos sin agrupar (ruido): {noise_count}")

    if not clusters:
        log_step("âš ï¸ DBSCAN no generÃ³ clusters vÃ¡lidos")
        log_step("ðŸ”„ Usando algoritmo greedy como fallback...")
        
        id_to_row = {r["photo_id"]: r for r in rows}
        greedy_clusters = fallback_greedy_clusters(rows, time_window_hours, min_cos)
        
        if not greedy_clusters:
            log_step("âŒ No se pudieron generar clusters. Considera:")
            log_step("   - Reducir cluster_cosine_threshold")
            log_step("   - Aumentar cluster_time_window_hours")
            log_step("   - Reducir dbscan_min_samples")
            restore_verified_events(conn)
            conn.close()
            return
        
        created = 0
        for photo_ids in greedy_clusters:
            cluster_rows = [id_to_row[pid] for pid in photo_ids if pid in id_to_row]
            name, start, end = summarize_cluster(cluster_rows)
            ev_id = create_event(conn, name, start, end)
            assign_photos_to_event(conn, ev_id, photo_ids)
            created += 1
        
        restore_verified_events(conn)
        
        log_step("=" * 60)
        log_step(f"âœ… FASE 3 COMPLETADA (Fallback Greedy)")
        log_step(f"ðŸ“ Eventos creados: {created}")
        if verified_events:
            log_step(f"ðŸ“Œ Eventos verificados preservados: {len(verified_events)}")
        log_step("=" * 60)
        conn.close()
        return

    id_to_row = {r["photo_id"]: r for r in rows}
    created = 0
    
    cluster_sizes = [len(photo_ids) for photo_ids in clusters.values()]
    log_step(f"ðŸ“ˆ TamaÃ±o de clusters:")
    log_step(f"   - MÃ­nimo: {min(cluster_sizes)}")
    log_step(f"   - MÃ¡ximo: {max(cluster_sizes)}")
    log_step(f"   - Promedio: {sum(cluster_sizes) / len(cluster_sizes):.1f}")
    
    spinner4 = Spinner("Creando eventos en BD...")
    spinner4.start()
    
    for cluster_id, photo_ids in clusters.items():
        cluster_rows = [id_to_row[pid] for pid in photo_ids if pid in id_to_row]
        
        if not cluster_rows:
            continue
        
        name, start, end = summarize_cluster(cluster_rows)
        ev_id = create_event(conn, name, start, end)
        assign_photos_to_event(conn, ev_id, photo_ids)
        created += 1
    
    spinner4.stop(f"Eventos creados: {created}")

    merged_count = auto_merge_similar_events(conn, cfg, id_to_row)

    # NUEVO: Restaurar eventos verificados
    restore_verified_events(conn)

    log_step("=" * 60)
    log_step("âœ… FASE 3 COMPLETADA (DBSCAN)")
    log_step(f"ðŸ“ Eventos iniciales: {created}")
    if merged_count > 0:
        log_step(f"ðŸ”— Fusiones: {merged_count} pares")
        final_count = created - merged_count
        log_step(f"ðŸ“ Eventos finales: {final_count}")
    if verified_events:
        log_step(f"ðŸ“Œ Eventos verificados preservados: {len(verified_events)}")
    log_step(f"ðŸ“¸ Fotos agrupadas: {sum(cluster_sizes)}")
    log_step(f"ðŸ“‡ Fotos sin agrupar: {noise_count}")
    log_step(f"â±ï¸ Tiempo total: {int(elapsed_total)}s")
    log_step("=" * 60)
    
    conn.close()


if __name__ == "__main__":
    run()
