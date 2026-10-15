import argparse
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

CFG_PATH = 'config.json'

def load_cfg():
    with open(CFG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def connect(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA foreign_keys = ON;')
    return conn

def print_stats(conn: sqlite3.Connection):
    cur = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    # Totales
    files_total = cur.execute('SELECT COUNT(*) FROM files').fetchone()[0]
    features = cur.execute('SELECT COUNT(*) FROM photo_features').fetchone()[0]
    events = cur.execute('SELECT COUNT(*) FROM events').fetchone()[0]
    event_photos = cur.execute('SELECT COUNT(*) FROM event_photos').fetchone()[0]

    # Rango temporal global de eventos
    try:
        start_end = cur.execute('SELECT MIN(start_time), MAX(end_time) FROM events').fetchone()
    except Exception:
        start_end = (None, None)

    # Top eventos por cantidad
    top = []
    try:
        top = cur.execute(
            '''
            SELECT e.id, COALESCE(e.name, e.suggested_name), e.start_time, e.end_time, COUNT(ep.photo_id) AS cnt
            FROM events e
            LEFT JOIN event_photos ep ON e.id = ep.event_id
            GROUP BY e.id
            ORDER BY cnt DESC
            LIMIT 5
            '''
        ).fetchall()
    except Exception:
        top = []

    print(f"\n[{now}] Progreso Fase 3")
    print(f"Files: {files_total} | Features: {features}")
    print(f"Eventos: {events} | event_photos asignados: {event_photos}")
    if start_end and (start_end[0] or start_end[1]):
        print(f"Rango temporal de eventos: {start_end[0]} a {start_end[1]}")
    if top:
        print("Top 5 eventos por cantidad:")
        for eid, name, st, en, cnt in top:
            print(f"  Evento {eid}: {name or 'Sin nombre'} | {cnt} fotos | {st} -> {en}")


def main():
    ap = argparse.ArgumentParser(description='Monitoreo de progreso de clustering (Fase 3)')
    ap.add_argument('--watch', type=int, default=0, help='Refrescar cada N segundos (0 = una sola vez)')
    args = ap.parse_args()

    cfg = load_cfg()
    db_path = cfg['db_path']
    conn = connect(db_path)

    if args.watch and args.watch > 0:
        try:
            while True:
                os.system('cls')
                print_stats(conn)
                time.sleep(args.watch)
        except KeyboardInterrupt:
            pass
    else:
        print_stats(conn)

    conn.close()

if __name__ == '__main__':
    main()