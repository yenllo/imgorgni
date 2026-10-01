import streamlit as st
import sqlite3
import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import shutil
import db
import numpy as np
import subprocess
import platform
from collections import Counter
from utils_tags import TagManager

# ConfiguraciÃ³n de pÃ¡gina
st.set_page_config(
    page_title="Organizador de Fotos IA",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        'About': "Organizador de Fototeca con IA - Powered by AMD Ryzen AI"
    }
)

# Estilos CSS mejorados
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        background: linear-gradient(120deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 1rem;
    }
    .stat-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1.5rem;
        border-radius: 10px;
        color: white;
        text-align: center;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    }
    .stat-number {
        font-size: 2.5rem;
        font-weight: bold;
    }
    .stat-label {
        font-size: 1rem;
        opacity: 0.9;
        margin-top: 0.5rem;
    }
    .event-card {
        border: 2px solid #e0e0e0;
        border-radius: 10px;
        padding: 1rem;
        margin-bottom: 1rem;
        transition: all 0.3s;
        background: white;
    }
    .event-card:hover {
        border-color: #667eea;
        box-shadow: 0 6px 16px rgba(102, 126, 234, 0.25);
        transform: translateY(-2px);
    }
    .success-box {
        background: #d4edda;
        border: 1px solid #c3e6cb;
        border-radius: 8px;
        padding: 1rem;
        color: #155724;
        margin: 1rem 0;
    }
    .warning-box {
        background: #fff3cd;
        border: 1px solid #ffeeba;
        border-radius: 8px;
        padding: 1rem;
        color: #856404;
        margin: 1rem 0;
    }
    .recommendation-card {
        background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
        border-radius: 10px;
        padding: 1rem;
        color: white;
        margin: 0.5rem 0;
    }
    .stButton>button {
        border-radius: 8px;
        font-weight: 500;
    }
</style>
""", unsafe_allow_html=True)


# ==================== FUNCIONES AUXILIARES ====================

@st.cache_resource
def load_config():
    cfg_path = Path("config.json")
    if not cfg_path.exists():
        st.error("âŒ No se encontrÃ³ config.json")
        st.stop()
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        st.error(f"âŒ Error en config.json: {e}")
        st.stop()

@st.cache_resource
def load_conn(db_path: str):
    conn = db.get_conn(db_path, check_same_thread=False) 
    conn.execute("PRAGMA cache_size=-524288")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA mmap_size=1073741824")
    conn.execute("PRAGMA page_size=8192")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def get_display_path(path_str: str, root_dir: str) -> Optional[str]:
    VISIBLE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    try:
        p = Path(path_str)
        if not p.is_absolute():
            p = Path(root_dir) / p
        
        if p.exists() and p.suffix.lower() in VISIBLE_EXTS:
            return str(p)
        
        parent = p.parent
        if not parent.exists(): 
            return None
            
        stem = p.stem
        for ext in VISIBLE_EXTS:
            candidate = parent / f"{stem}{ext}"
            if candidate.exists():
                return str(candidate)
        return None
    except Exception:
        return None

def open_file_location(file_path: str):
    try:
        file_path = Path(file_path).resolve()
        if file_path.is_file():
            path_to_open = str(file_path)
            is_file = True
        else:
            path_to_open = str(file_path.parent)
            is_file = False

        if not Path(path_to_open).exists(): 
            return False

        system = platform.system()
        if system == "Windows":
            if is_file:
                subprocess.run(["explorer", "/select,", path_to_open])
            else:
                subprocess.run(["explorer", path_to_open])
        elif system == "Darwin":
            if is_file:
                subprocess.run(["open", "-R", path_to_open])
            else:
                subprocess.run(["open", path_to_open])
        else:
            subprocess.run(["xdg-open", path_to_open if not is_file else str(file_path.parent)])
        return True
    except Exception:
        return False

@st.cache_data(ttl=60, max_entries=5)
def get_database_stats(_conn):
    stats = {}
    cur = _conn.execute("SELECT COUNT(*) FROM files")
    stats['total_photos'] = cur.fetchone()[0]
    cur = _conn.execute("SELECT COUNT(*) FROM events")
    stats['total_events'] = cur.fetchone()[0]
    cur = _conn.execute("SELECT COUNT(DISTINCT photo_id) FROM event_photos")
    stats['photos_in_events'] = cur.fetchone()[0]
    stats['ungrouped_photos'] = stats['total_photos'] - stats['photos_in_events']
    cur = _conn.execute("SELECT COUNT(*) FROM photo_features")
    stats['analyzed_photos'] = cur.fetchone()[0]
    return stats

def get_event_thumbnail(conn, event_id: int, root_dir: str) -> Optional[str]:
    cache_key = f"thumb_{event_id}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]
    
    cur = conn.execute("SELECT f.file_path FROM event_photos ep JOIN files f ON ep.photo_id = f.id WHERE ep.event_id = ? LIMIT 1", (event_id,))
    row = cur.fetchone()
    if row:
        thumb = get_display_path(row[0], root_dir)
        st.session_state[cache_key] = thumb
        return thumb
    return None

def format_date_range(start: str, end: str) -> str:
    try:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
        if start_dt.date() == end_dt.date():
            return start_dt.strftime("%d %b %Y")
        elif (end_dt - start_dt).days <= 1:
            return f"{start_dt.strftime('%d %b %Y')} ({start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')})"
        else:
            return f"{start_dt.strftime('%d %b')} - {end_dt.strftime('%d %b %Y')}"
    except:
        return f"{start} â†’ {end}"

# ==================== FUNCIONES PARA TAGS ====================

@st.cache_resource
def get_tag_manager(_conn):
    """Obtiene instancia del TagManager"""
    return TagManager(_conn)

def export_tags_to_csv(tag_manager: TagManager, output_path: str):
    """Exporta todos los tags a un archivo CSV"""
    import csv
    
    tags = tag_manager.get_all_tags()
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['tag_name', 'category', 'count', 'first_seen', 'last_seen'])
        writer.writeheader()
        writer.writerows([{'tag_name': t['name'], 'category': t['category'], 'count': t['count'], 'first_seen': t['first_seen'], 'last_seen': t['last_seen']} for t in tags])
    
    return len(tags)

# ==================== INICIALIZACIÃ“N ====================

cfg = load_config()
conn = load_conn(cfg["db_path"])
tag_manager = get_tag_manager(conn)

# Estado de sesiÃ³n
if 'current_page' not in st.session_state:
    st.session_state.current_page = 'dashboard'
if 'selected_event_id' not in st.session_state:
    st.session_state.selected_event_id = None
if 'selected_photos' not in st.session_state:
    st.session_state.selected_photos = set()
if 'photo_page' not in st.session_state:
    st.session_state.photo_page = 0

# ==================== SIDEBAR ====================
with st.sidebar:
    st.markdown("### ðŸ“¸ Photo Organizer AI")
    st.markdown("---")
    
    pages = {
        "ðŸ  Dashboard": "dashboard",
        "ðŸ“ Eventos": "eventos",
        "ðŸ” BÃºsqueda": "busqueda",
        "âš™ï¸ ConfiguraciÃ³n": "config"
    }
    
    for label, page_id in pages.items():
        if st.button(label, use_container_width=True, type="primary" if st.session_state.current_page == page_id else "secondary"):
            st.session_state.current_page = page_id
            st.rerun()
    
    st.markdown("---")
    
    if st.button("ðŸ”„ Actualizar", use_container_width=True):
        get_database_stats.clear()
        get_tag_manager.clear()
        
        for key in list(st.session_state.keys()):
            if key.startswith('thumb_'):
                del st.session_state[key]
        st.rerun()
    
    st.markdown("---")
    
    stats = get_database_stats(conn)
    st.metric("Total Fotos", f"{stats['total_photos']:,}")
    st.metric("Eventos", f"{stats['total_events']:,}")
    st.metric("Sin agrupar", f"{stats['ungrouped_photos']:,}")

# ==================== PÃGINA: DASHBOARD ====================
if st.session_state.current_page == 'dashboard':
    st.markdown('<p class="main-header">ðŸ“Š Dashboard</p>', unsafe_allow_html=True)
    
    stats = get_database_stats(conn)
    
    col1, col2, col3, col4 = st.columns(4)
    with col1: 
        st.markdown(f'<div class="stat-card"><div class="stat-number">{stats["total_photos"]:,}</div><div class="stat-label">Fotos Totales</div></div>', unsafe_allow_html=True)
    with col2: 
        st.markdown(f'<div class="stat-card" style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);"><div class="stat-number">{stats["total_events"]:,}</div><div class="stat-label">Eventos</div></div>', unsafe_allow_html=True)
    with col3: 
        st.markdown(f'<div class="stat-card" style="background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);"><div class="stat-number">{stats["photos_in_events"]:,}</div><div class="stat-label">Agrupadas</div></div>', unsafe_allow_html=True)
    with col4: 
        pct = (stats['analyzed_photos'] / stats['total_photos'] * 100) if stats['total_photos'] > 0 else 0
        st.markdown(f'<div class="stat-card" style="background: linear-gradient(135deg, #43e97b 0%, #38f9d7 100%);"><div class="stat-number">{pct:.1f}%</div><div class="stat-label">Analizadas</div></div>', unsafe_allow_html=True)
    
    st.markdown("---")
    
    st.subheader("ðŸ·ï¸ Tags MÃ¡s Frecuentes")
    top_tags_dashboard = tag_manager.get_top_tags(limit=10)
    
    if top_tags_dashboard:
        cols = st.columns(5)
        for idx, (tag, count) in enumerate(top_tags_dashboard):
            with cols[idx % 5]:
                st.metric(tag, f"{count:,}")
    else:
        st.info("No hay tags disponibles. Ejecuta la Fase 2 para analizar las fotos.")
    
    st.markdown("---")
    
    st.subheader("ðŸ• Eventos Recientes")
    cur = conn.execute("SELECT e.id, COALESCE(e.name, e.suggested_name) AS display_name, e.start_time, e.end_time, COUNT(ep.photo_id) as photo_count FROM events e LEFT JOIN event_photos ep ON e.id = ep.event_id GROUP BY e.id ORDER BY e.start_time DESC LIMIT 6")
    recent_events = cur.fetchall()
    
    if recent_events:
        cols = st.columns(3)
        for i, event_row in enumerate(recent_events):
            event = {'id': event_row[0], 'name': event_row[1], 'start_time': event_row[2], 'end_time': event_row[3], 'count': event_row[4]}
            with cols[i % 3]:
                thumb = get_event_thumbnail(conn, event['id'], cfg['root_dir'])
                if thumb: 
                    st.image(thumb, use_container_width=True)
                else: 
                    st.image("https://via.placeholder.com/300x200/cccccc/666666?text=Sin+Imagen", use_container_width=True)
                st.markdown(f"**{event['name']}**")
                st.caption(f"ðŸ“… {format_date_range(event['start_time'], event['end_time'])}")
                st.caption(f"ðŸ“¸ {event['count']} fotos")
                if st.button("Ver", key=f"dash_view_{event['id']}", use_container_width=True):
                    st.session_state.selected_event_id = event['id']
                    st.session_state.current_page = 'event_detail'
                    st.session_state.photo_page = 0
                    st.rerun()
    else:
        st.info("No hay eventos. Ejecuta la Fase 3 (agrupaciÃ³n).")

# ==================== PÃGINA: EVENTOS ====================
elif st.session_state.current_page == 'eventos':
    st.markdown('<p class="main-header">ðŸ“ GestiÃ³n de Eventos</p>', unsafe_allow_html=True)
    cur = conn.execute("SELECT e.id, COALESCE(e.name, e.suggested_name) AS display_name, e.start_time, e.end_time, COUNT(ep.photo_id) as photo_count, CASE WHEN e.name IS NOT NULL THEN 1 ELSE 0 END AS is_custom FROM events e LEFT JOIN event_photos ep ON e.id = ep.event_id GROUP BY e.id ORDER BY e.start_time DESC")
    events = [{'id': r[0], 'name': r[1], 'start_time': r[2], 'end_time': r[3], 'count': r[4], 'is_custom': r[5]} for r in cur.fetchall()]
    
    search_term = st.text_input("ðŸ” Buscar", placeholder="Nombre del evento...")
    filtered = [e for e in events if search_term.lower() in e['name'].lower()] if search_term else events
    
    st.caption(f"**{len(filtered)}** eventos encontrados")
    st.markdown("---")
    
    cols_per_row = 3
    for i in range(0, len(filtered), cols_per_row):
        cols = st.columns(cols_per_row)
        for j, col in enumerate(cols):
            if i + j < len(filtered):
                event = filtered[i + j]
                with col:
                    thumb = get_event_thumbnail(conn, event['id'], cfg['root_dir'])
                    if thumb: 
                        st.image(thumb, use_container_width=True)
                    else: 
                        st.image("https://via.placeholder.com/300x200/cccccc/666666?text=?", use_container_width=True)
                    emoji_prefix = "âœï¸ " if event['is_custom'] else "ðŸ¤– "
                    st.markdown(f"**{emoji_prefix}{event['name']}**")
                    st.caption(f"ðŸ“… {format_date_range(event['start_time'], event['end_time'])}")
                    st.caption(f"ðŸ“¸ {event['count']} fotos")
                    if st.button("ðŸ‘ï¸ Ver", key=f"grid_view_{event['id']}", use_container_width=True):
                        st.session_state.selected_event_id = event['id']
                        st.session_state.current_page = 'event_detail'
                        st.session_state.photo_page = 0
                        st.rerun()

# ==================== PÃGINA: BÃšSQUEDA ====================
elif st.session_state.current_page == 'busqueda':
    st.markdown('<p class="main-header">ðŸ” BÃºsqueda Avanzada</p>', unsafe_allow_html=True)
    
    st.subheader("Buscar por Tags")
    
    tag_stats = tag_manager.get_tag_stats()
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Tags Ãšnicos", f"{tag_stats['total_unique_tags']:,}")
    with col2:
        st.metric("Total Ocurrencias", f"{tag_stats['total_occurrences']:,}")
    with col3:
        st.metric("CategorÃ­as", tag_stats['total_categories'])
    
    st.markdown("---")
    
    search_query = st.text_input("ðŸ·ï¸ Buscar tag", placeholder="person, car, dog...")
    
    if search_query:
        results = tag_manager.search_tags(search_query, limit=20)
        
        if results:
            st.success(f"âœ… {len(results)} tags encontrados")
            
            for tag_data in results:
                col_name, col_count = st.columns([3, 1])
                with col_name:
                    st.write(f"**{tag_data['name']}** - {tag_data['category']}")
                with col_count:
                    st.metric("", tag_data['count'])
        else:
            st.info("No se encontraron tags")

# ==================== PÃGINA: CONFIGURACIÃ“N ====================
elif st.session_state.current_page == 'config':
    st.markdown('<p class="main-header">âš™ï¸ ConfiguraciÃ³n</p>', unsafe_allow_html=True)
    
    st.subheader("ðŸ“ Rutas del Sistema")
    
    st.markdown("**Directorio raÃ­z de fotos:**")
    st.code(cfg['root_dir'], language="text")
    
    st.markdown("**Base de datos:**")
    st.code(cfg['db_path'], language="text")
    
    st.markdown("---")
    
    st.subheader("ðŸ› ï¸ Mantenimiento")
    
    if st.button("ðŸ”§ VACUUM (Optimizar BD)", use_container_width=True):
        with st.spinner("Optimizando..."):
            conn.execute("VACUUM")
            st.success("âœ… Base de datos optimizada")

else:
    st.info("PÃ¡gina no encontrada")
