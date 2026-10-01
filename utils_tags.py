"""
Sistema de gestiÃ³n de tags con detecciÃ³n automÃ¡tica
"""

import json
import sqlite3
from pathlib import Path
from typing import List, Set, Dict, Tuple
from collections import Counter
import numpy as np
from datetime import datetime # Added import

# Lista expandida de objetos COCO + custom
EXTENDED_COCO_CLASSES = [
    # Personas y partes del cuerpo
    "person", "face", "hand", "child", "baby", "adult",
    # VehÃ­culos
    "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "ship", "yacht", "helicopter",
    # TrÃ¡fico
    "traffic light", "fire hydrant", "stop sign", "parking meter",
    # Mobiliario exterior
    "bench", "fountain", "statue",
    # Animales
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "lion", "tiger", "monkey", "rabbit", "fish", "dolphin",
    # Accesorios
    "backpack", "umbrella", "handbag", "tie", "suitcase", "briefcase", "purse",
    # Deportes
    "frisbee", "skis", "snowboard", "sports ball", "kite", "baseball bat",
    "baseball glove", "skateboard", "surfboard", "tennis racket", "golf club",
    "basketball", "football", "soccer ball",
    # Comida y bebida
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl",
    "banana", "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog",
    "pizza", "donut", "cake", "bread", "cheese", "salad", "coffee", "tea",
    # Muebles
    "chair", "couch", "potted plant", "bed", "dining table", "toilet", "desk",
    "cabinet", "shelf", "lamp",
    # ElectrÃ³nica
    "tv", "laptop", "mouse", "remote", "keyboard", "cell phone", "camera",
    "monitor", "tablet", "headphones", "speaker",
    # ElectrodomÃ©sticos
    "microwave", "oven", "toaster", "sink", "refrigerator", "dishwasher",
    "washing machine",
    # Objetos del hogar
    "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
    "mirror", "picture frame", "candle", "pillow", "blanket",
    # Naturaleza
    "tree", "flower", "grass", "mountain", "beach", "ocean", "lake", "river",
    "sky", "cloud", "sun", "moon", "star", "sunset", "sunrise",
    # Edificios
    "building", "house", "window", "door", "roof", "bridge", "tower",
    # Eventos
    "birthday", "wedding", "party", "concert", "festival", "graduation",
    # Actividades
    "cooking", "reading", "writing", "dancing", "singing", "playing",
    "swimming", "running", "hiking", "camping",
    # Clima
    "rain", "snow", "fog", "storm",
    # Interior
    "room", "kitchen", "bathroom", "bedroom", "living room", "office",
    # Abstractos
    "selfie", "portrait", "landscape", "group photo", "night", "day",
    "indoor", "outdoor", "close-up", "wide shot"
]


class TagManager:
    """Gestor centralizado de tags que usa una conexiÃ³n DB existente."""

    def __init__(self, conn: sqlite3.Connection):
        """Inicializa usando una conexiÃ³n SQLite existente."""
        # self.db_path = db_path # No longer needed
        self.conn = conn # Use the provided connection
        self._init_tag_tables()

    def _init_tag_tables(self):
        """Inicializa tablas de tags si no existen."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tag_registry (
                    tag_name TEXT PRIMARY KEY,
                    category TEXT,
                    count INTEGER DEFAULT 0,
                    first_seen TEXT,
                    last_seen TEXT
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tag_count ON tag_registry(count DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tag_category ON tag_registry(category)")
            # Do NOT commit here, let the main script handle commits

            # Check if table is empty without committing changes yet
            cursor.execute("SELECT COUNT(*) FROM tag_registry")
            if cursor.fetchone()[0] == 0:
                self._populate_base_tags(cursor) # Pass cursor to populate without commit
        except sqlite3.Error as e:
            print(f"Error initializing tag tables: {e}")
            # Should probably raise the error or handle it more robustly

    def _populate_base_tags(self, cursor: sqlite3.Cursor):
        """Pobla la tabla con tags base de COCO (sin commit)."""
        now = datetime.now().isoformat()
        categories = {
            "person": ["person", "face", "hand", "child", "baby", "adult"],
            "vehicle": ["bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat"],
            "animal": ["bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear"],
            "food": ["banana", "apple", "sandwich", "orange", "pizza", "cake", "bread"],
            "furniture": ["chair", "couch", "bed", "table", "desk"],
            "electronics": ["tv", "laptop", "phone", "camera", "keyboard"],
            "nature": ["tree", "flower", "mountain", "beach", "ocean", "sky"],
            "building": ["building", "house", "bridge", "tower"],
        }
        rows = []
        for tag in EXTENDED_COCO_CLASSES:
            category = "other"
            for cat, tags in categories.items():
                if tag in tags:
                    category = cat
                    break
            rows.append((tag, category, 0, now, now))

        try:
            cursor.executemany(
                "INSERT OR IGNORE INTO tag_registry (tag_name, category, count, first_seen, last_seen) VALUES (?, ?, ?, ?, ?)",
                rows
            )
        except sqlite3.Error as e:
            print(f"Error populating base tags: {e}")
        # No commit here

    def register_tags_nocommit(self, tags: List[str]) -> List[str]:
        """
        Registra/Actualiza tags detectados SIN hacer commit.
        Devuelve lista de tags normalizados.
        """
        now = datetime.now().isoformat()
        normalized_tags = []
        cursor = self.conn.cursor()
        try:
            for tag in tags:
                tag = tag.lower().strip()
                if not tag or len(tag) < 2:
                    continue
                normalized_tags.append(tag)
                # Insertar o actualizar en registry (UPSERT)
                cursor.execute("""
                    INSERT INTO tag_registry (tag_name, category, count, first_seen, last_seen)
                    VALUES (?, 'auto-detected', 1, ?, ?)
                    ON CONFLICT(tag_name) DO UPDATE SET
                        count = count + 1,
                        last_seen = excluded.last_seen
                """, (tag, now, now)) # Use excluded.last_seen for consistency on update
        except sqlite3.Error as e:
            print(f"Error registering tags (nocommit): {e}")
            # Decide if you want to return partial results or raise error
            return tags # Return original tags on error maybe? Or empty list?
        # No commit here
        return normalized_tags

    # --- MÃ©todos que SÃ hacen commit (usados por UI/scripts externos) ---
    # Wrap these in try-except and ensure they commit

    def register_tags(self, tags: List[str]) -> List[str]:
        """Registra/Actualiza tags y HACE commit."""
        normalized_tags = self.register_tags_nocommit(tags)
        try:
            self.conn.commit()
        except sqlite3.Error as e:
            print(f"Error committing after register_tags: {e}")
        return normalized_tags

    def get_all_tags(self, min_count: int = 0) -> List[Dict]:
        """Obtiene todos los tags con sus estadÃ­sticas."""
        try:
            cur = self.conn.execute("""
                SELECT tag_name, category, count, first_seen, last_seen
                FROM tag_registry WHERE count >= ? ORDER BY count DESC
            """, (min_count,))
            return [{'name': r[0],'category': r[1],'count': r[2],'first_seen': r[3],'last_seen': r[4]} for r in cur.fetchall()]
        except sqlite3.Error as e:
            print(f"Error getting all tags: {e}")
            return []

    def search_tags(self, query: str, limit: int = 20) -> List[Dict]:
        """Busca tags que coincidan con el query."""
        try:
            cur = self.conn.execute("""
                SELECT tag_name, category, count FROM tag_registry
                WHERE tag_name LIKE ? ORDER BY count DESC, tag_name ASC LIMIT ?
            """, (f"%{query.lower()}%", limit))
            return [{'name': r[0], 'category': r[1], 'count': r[2]} for r in cur.fetchall()]
        except sqlite3.Error as e:
            print(f"Error searching tags: {e}")
            return []

    def get_top_tags(self, limit: int = 50, category: str = None) -> List[Tuple[str, int]]:
        """Obtiene los tags mÃ¡s frecuentes."""
        sql = "SELECT tag_name, count FROM tag_registry "
        params = []
        if category:
            sql += "WHERE category = ? "
            params.append(category)
        sql += "ORDER BY count DESC LIMIT ?"
        params.append(limit)
        try:
            cur = self.conn.execute(sql, tuple(params))
            return [(r[0], r[1]) for r in cur.fetchall()]
        except sqlite3.Error as e:
            print(f"Error getting top tags: {e}")
            return []

    def get_tag_stats(self) -> Dict:
        """Obtiene estadÃ­sticas generales de tags."""
        stats = {'total_unique_tags': 0, 'total_occurrences': 0, 'total_categories': 0}
        try:
            cur = self.conn.execute("SELECT COUNT(*), SUM(count) FROM tag_registry")
            total_unique, total_occurrences = cur.fetchone()
            stats['total_unique_tags'] = total_unique or 0
            stats['total_occurrences'] = total_occurrences or 0
            cur = self.conn.execute("SELECT COUNT(DISTINCT category) FROM tag_registry")
            stats['total_categories'] = cur.fetchone()[0] or 0
        except sqlite3.Error as e:
            print(f"Error getting tag stats: {e}")
        return stats

    def merge_tags(self, old_tag: str, new_tag: str):
        """Fusiona un tag antiguo en uno nuevo y HACE commit."""
        old_tag = old_tag.lower().strip()
        new_tag = new_tag.lower().strip()
        if not old_tag or not new_tag or old_tag == new_tag:
            return

        try:
            cursor = self.conn.cursor()
            # 1. Update photo_features table
            cursor.execute("SELECT photo_id, tags_json FROM photo_features WHERE tags_json LIKE ?", (f'%"{old_tag}"%',))
            updates = []
            for photo_id, tags_json in cursor.fetchall():
                try:
                    tags = json.loads(tags_json or "[]")
                    if old_tag in tags:
                        tags = [new_tag if t == old_tag else t for t in tags]
                        tags = sorted(list(set(tags))) # Remove duplicates and sort
                        updates.append((json.dumps(tags), photo_id))
                except json.JSONDecodeError:
                    continue # Skip malformed JSON
            if updates:
                cursor.executemany("UPDATE photo_features SET tags_json = ? WHERE photo_id = ?", updates)

            # 2. Update tag_registry
            cursor.execute("SELECT count FROM tag_registry WHERE tag_name = ?", (old_tag,))
            old_count_row = cursor.fetchone()
            if old_count_row:
                old_count = old_count_row[0]
                # Add old count to new tag, or insert new tag if it doesn't exist
                cursor.execute("""
                    INSERT INTO tag_registry (tag_name, category, count, first_seen, last_seen)
                    VALUES (?, (SELECT category FROM tag_registry WHERE tag_name = ? LIMIT 1), ?, datetime('now'), datetime('now'))
                    ON CONFLICT(tag_name) DO UPDATE SET count = count + excluded.count
                """, (new_tag, new_tag, old_count)) # Try to inherit category, add count
                # Delete the old tag
                cursor.execute("DELETE FROM tag_registry WHERE tag_name = ?", (old_tag,))

            self.conn.commit()
            print(f"Merged tag '{old_tag}' into '{new_tag}'")

        except sqlite3.Error as e:
            print(f"Error merging tags '{old_tag}' into '{new_tag}': {e}")
            try: self.conn.rollback()
            except: pass

    def delete_tag(self, tag: str):
        """Elimina un tag de todas las fotos y del registry y HACE commit."""
        tag = tag.lower().strip()
        if not tag: return

        try:
            cursor = self.conn.cursor()
            # 1. Update photo_features
            cursor.execute("SELECT photo_id, tags_json FROM photo_features WHERE tags_json LIKE ?", (f'%"{tag}"%',))
            updates = []
            for photo_id, tags_json in cursor.fetchall():
                try:
                    tags = json.loads(tags_json or "[]")
                    if tag in tags:
                        tags = sorted([t for t in tags if t != tag])
                        updates.append((json.dumps(tags), photo_id))
                except json.JSONDecodeError:
                    continue
            if updates:
                cursor.executemany("UPDATE photo_features SET tags_json = ? WHERE photo_id = ?", updates)

            # 2. Delete from registry
            cursor.execute("DELETE FROM tag_registry WHERE tag_name = ?", (tag,))

            self.conn.commit()
            print(f"Deleted tag '{tag}'")
        except sqlite3.Error as e:
            print(f"Error deleting tag '{tag}': {e}")
            try: self.conn.rollback()
            except: pass


    def get_related_tags(self, tag: str, limit: int = 10) -> List[Tuple[str, float]]:
        """Encuentra tags relacionados que suelen aparecer juntos."""
        tag = tag.lower().strip()
        if not tag: return []

        co_occurrences = Counter()
        tag_count = 0
        try:
            # Query might be slow on large DBs, consider optimization if needed
            cursor = self.conn.execute("SELECT tags_json FROM photo_features WHERE tags_json LIKE ?", (f'%"{tag}"%',))
            for (tags_json,) in cursor.fetchall():
                try:
                    tags = json.loads(tags_json or "[]")
                    if tag in tags:
                        tag_count += 1
                        for other_tag in tags:
                            if other_tag != tag:
                                co_occurrences[other_tag] += 1
                except json.JSONDecodeError:
                    continue
        except sqlite3.Error as e:
            print(f"Error getting related tags for '{tag}': {e}")
            return []

        if tag_count == 0:
            return []

        # Calculate scores (relative frequency)
        related = [
            (other_tag, count / tag_count)
            for other_tag, count in co_occurrences.most_common(limit)
        ]
        return related

    def close(self):
        """Cierra la conexiÃ³n (ya no es necesario si se usa conexiÃ³n externa)."""
        # self.conn.close() # Connection is managed externally now
        pass