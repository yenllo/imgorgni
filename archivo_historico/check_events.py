import json
import sqlite3
from statistics import mean


def main(cfg_path: str = "c:/Script/Organizador2/config.json"):
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    db_path = cfg.get("db_path", "c:/Script/Organizador2/photos.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Count events
    cur.execute("SELECT COUNT(*) FROM events")
    total_events = cur.fetchone()[0]

    # Sizes per event
    cur.execute("SELECT event_id, COUNT(*) AS cnt FROM event_photos GROUP BY event_id ORDER BY cnt ASC")
    rows = cur.fetchall()
    sizes = [r[1] for r in rows]
    small = [(eid, cnt) for eid, cnt in rows if cnt <= 4]

    print(f"DB: {db_path}")
    print(f"Total eventos: {total_events}")
    if sizes:
        print(f"TamaÃ±os de evento: min={min(sizes)}, max={max(sizes)}, avg={mean(sizes):.2f}")
    else:
        print("No hay asignaciones en event_photos.")

    print(f"Eventos con â‰¤4 fotos: {len(small)}")
    if small:
        # Mostrar los 20 primeros para inspecciÃ³n rÃ¡pida
        for eid, cnt in small[:20]:
            print(f"- Event {eid}: {cnt} fotos")

    # Top grandes (opcional)
    top_big = sorted(rows, key=lambda x: x[1], reverse=True)[:10]
    if top_big:
        print("Top eventos por tamaÃ±o:")
        for eid, cnt in top_big:
            print(f"- Event {eid}: {cnt} fotos")

    conn.close()


if __name__ == "__main__":
    main()