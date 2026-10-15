"""
Capa de acceso a SQLite del organizador de fotos.
Reconstruido a partir de los usos en 1_indexado.py, 2_analisis.py,
3_agrupacion_mejorado.py, app.py y utils_tags.py.

Esquema (verificado contra la BD real de 26k fotos en "E:/X pero importante/GIT/photos.db"):
  files(id, file_path UNIQUE, capture_date, camera_model, file_hash_sha256, needs_analysis)
  photo_features(photo_id PK->files.id, tags_json, visual_embedding)
  events(id, suggested_name, name, start_time, end_time)
  event_photos(event_id, photo_id) PK compuesta
"""
import json
import sqlite3
from typing import List, Tuple, Dict, Optional


class DiscoCaido(RuntimeError):
    """El NAS no esta montado. Se levanta antes de tocar la base, no despues."""


def verificar_disco_montado(root_path: str, exigir: str = "Fotos/Galeria") -> None:
    """Corta si el NAS no responde, ANTES de marcar huerfanos o borrar nada.

    El 15/10/2026 a las 07:42 un corte de luz reinicio la Pi y de paso tumbo un
    segundo el gabinete USB del 8TB. En ese instante `os.path.exists()` devolvia
    False para TODO, y un script que marcaba ausentes habria dado por perdidas
    las 139.760 filas de la base. No alcanza con que exista la raiz: una unidad
    de red caida o un automount a medias deja el punto de montaje visible pero
    vacio, por eso ademas se exige una carpeta conocida con algo adentro.
    """
    import os

    raiz = os.path.abspath(root_path)
    if not os.path.isdir(raiz):
        raise DiscoCaido("el NAS no esta montado: no existe %s" % raiz)
    adentro = os.path.join(raiz, *exigir.replace("\\", "/").split("/"))
    if not os.path.isdir(adentro):
        raise DiscoCaido("el NAS respondio pero falta %s: montaje a medias" % adentro)
    try:
        next(os.scandir(adentro))
    except StopIteration:
        raise DiscoCaido("%s esta vacia: el disco no termino de montar" % adentro)
    except OSError as e:
        raise DiscoCaido("no puedo leer %s: %s" % (adentro, e))


def get_conn(db_path: str, check_same_thread: bool = True, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True,
                               check_same_thread=check_same_thread)
    else:
        conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT UNIQUE NOT NULL,
            capture_date TEXT,
            camera_model TEXT,
            file_hash_sha256 TEXT,
            needs_analysis INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_files_sha ON files(file_hash_sha256);
        CREATE INDEX IF NOT EXISTS idx_files_needs ON files(needs_analysis);

        CREATE TABLE IF NOT EXISTS photo_features (
            photo_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
            tags_json TEXT,
            visual_embedding TEXT
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suggested_name TEXT,
            name TEXT,
            start_time TEXT,
            end_time TEXT
        );

        CREATE TABLE IF NOT EXISTS event_photos (
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            photo_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            PRIMARY KEY (event_id, photo_id)
        );

        CREATE TABLE IF NOT EXISTS faces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            person_id INTEGER,
            embedding_json TEXT,
            bbox_json TEXT,
            det_score REAL
        );
        CREATE INDEX IF NOT EXISTS idx_faces_photo ON faces(photo_id);
        CREATE INDEX IF NOT EXISTS idx_faces_person ON faces(person_id);

        CREATE TABLE IF NOT EXISTS persons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            face_count INTEGER DEFAULT 0
        );
    """)
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection):
    """Agrega columnas nuevas a BDs viejas. ALTER TABLE ADD COLUMN es barato en SQLite."""
    extra = [
        ("files", "gps_lat", "REAL"),
        ("files", "gps_lon", "REAL"),
        ("files", "place", "TEXT"),
        ("photo_features", "scene_tags", "TEXT"),
        ("photo_features", "quality_json", "TEXT"),
        ("photo_features", "phash", "TEXT"),
        ("persons", "rep_face_id", "INTEGER"),
        ("duplicados", "ambos_corruptos", "INTEGER DEFAULT 0"),
    ]
    for table, column, coltype in extra:
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        # ponytail: table_info vacío = tabla inexistente (SQLite no permite tablas sin columnas).
        # 'duplicados' la crea web_curador.migrar(), puede no existir todavía.
        if existing and column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def enable_fast_writes(conn: sqlite3.Connection, cache_mb: int = 512):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA cache_size=-{int(cache_mb) * 1024}")  # negativo = KB
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA mmap_size=1073741824")


# ---------- Fase 1: indexado ----------

def fetch_all_hashes(conn: sqlite3.Connection) -> List[str]:
    cur = conn.execute("SELECT file_hash_sha256 FROM files WHERE file_hash_sha256 IS NOT NULL")
    return [r[0] for r in cur.fetchall()]


def fetch_all_paths(conn: sqlite3.Connection) -> List[str]:
    cur = conn.execute("SELECT file_path FROM files")
    return [r[0] for r in cur.fetchall()]


def upsert_file_nocommit(cur: sqlite3.Cursor, file_path: str, capture_date: Optional[str],
                         camera_model: Optional[str], sha256: str, needs_analysis: int = 1):
    cur.execute("""
        INSERT INTO files (file_path, capture_date, camera_model, file_hash_sha256, needs_analysis)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET
            capture_date=excluded.capture_date,
            camera_model=excluded.camera_model,
            file_hash_sha256=excluded.file_hash_sha256,
            needs_analysis=excluded.needs_analysis
    """, (file_path, capture_date, camera_model, sha256, needs_analysis))


# ---------- Fase 2: análisis ----------

def fetch_files_needing_analysis(conn: sqlite3.Connection) -> List[Tuple[int, str]]:
    cur = conn.execute("SELECT id, file_path FROM files WHERE needs_analysis=1 ORDER BY id")
    return cur.fetchall()


def upsert_features_nocommit(conn: sqlite3.Connection, photo_id: int,
                             tags: List[str], embedding: List[float]):
    conn.execute("""
        INSERT INTO photo_features (photo_id, tags_json, visual_embedding)
        VALUES (?, ?, ?)
        ON CONFLICT(photo_id) DO UPDATE SET
            tags_json=excluded.tags_json,
            visual_embedding=excluded.visual_embedding
    """, (photo_id, json.dumps(tags or []), json.dumps(embedding or [])))


def set_needs_analysis_nocommit(conn: sqlite3.Connection, photo_id: int, value: int):
    conn.execute("UPDATE files SET needs_analysis=? WHERE id=?", (value, photo_id))


# ---------- Análisis extendido (escenas, calidad, phash, GPS, caras) ----------

def upsert_extras_nocommit(conn: sqlite3.Connection, photo_id: int,
                           scene_tags: List[str] = None, quality: Dict = None,
                           phash: str = None):
    conn.execute("""
        UPDATE photo_features SET scene_tags=?, quality_json=?, phash=? WHERE photo_id=?
    """, (json.dumps(scene_tags or []), json.dumps(quality or {}), phash, photo_id))


def set_gps_nocommit(conn: sqlite3.Connection, photo_id: int,
                     lat: Optional[float], lon: Optional[float], place: Optional[str]):
    conn.execute("UPDATE files SET gps_lat=?, gps_lon=?, place=? WHERE id=?",
                 (lat, lon, place, photo_id))


def insert_faces_nocommit(conn: sqlite3.Connection, photo_id: int, faces: List[Dict]):
    if not faces:
        return
    conn.executemany("""
        INSERT INTO faces (photo_id, embedding_json, bbox_json, det_score)
        VALUES (?, ?, ?, ?)
    """, [(photo_id, json.dumps(f["embedding"]), json.dumps(f["bbox"]),
           f.get("det_score")) for f in faces])


def fetch_all_faces(conn: sqlite3.Connection) -> List[Tuple[int, int, List[float]]]:
    """Devuelve [(face_id, photo_id, embedding)] para clustering de personas."""
    out = []
    for fid, pid, emb_json in conn.execute(
            "SELECT id, photo_id, embedding_json FROM faces WHERE embedding_json IS NOT NULL"):
        try:
            out.append((fid, pid, json.loads(emb_json)))
        except json.JSONDecodeError:
            continue
    return out


def set_face_persons(conn: sqlite3.Connection, assignments: List[Tuple[int, int]]):
    """assignments: [(face_id, person_id)]"""
    conn.executemany("UPDATE faces SET person_id=? WHERE id=?",
                     [(pid, fid) for fid, pid in assignments])
    conn.commit()


# ---------- Fase 3: clustering ----------

def fetch_features_with_time(conn: sqlite3.Connection) -> List[Dict]:
    cur = conn.execute("""
        SELECT f.id, f.capture_date, pf.tags_json, pf.visual_embedding,
               pf.scene_tags, f.place
        FROM files f JOIN photo_features pf ON f.id = pf.photo_id
    """)
    rows = []
    for photo_id, capture_date, tags_json, embedding_json, scene_json, place in cur.fetchall():
        def _load(raw):
            try:
                return json.loads(raw or "[]")
            except json.JSONDecodeError:
                return []
        rows.append({
            "photo_id": photo_id,
            "capture_date": capture_date,
            "tags": _load(tags_json),
            "embedding": _load(embedding_json),
            "scene_tags": _load(scene_json),
            "place": place,
        })
    return rows


def create_event(conn: sqlite3.Connection, suggested_name: str,
                 start_time: str, end_time: str) -> int:
    cur = conn.execute(
        "INSERT INTO events (suggested_name, name, start_time, end_time) VALUES (?, NULL, ?, ?)",
        (suggested_name, start_time, end_time))
    conn.commit()
    return cur.lastrowid


def assign_photos_to_event(conn: sqlite3.Connection, event_id: int, photo_ids: List[int]):
    conn.executemany(
        "INSERT OR IGNORE INTO event_photos (event_id, photo_id) VALUES (?, ?)",
        [(event_id, pid) for pid in photo_ids])
    conn.commit()
