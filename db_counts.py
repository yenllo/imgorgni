import sqlite3

DB_PATH = 'photos.db'

if __name__ == '__main__':
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute('SELECT COUNT(*) FROM files')
        total = cur.fetchone()[0]

        cur.execute('SELECT COUNT(*) FROM files WHERE needs_analysis = 1')
        pending = cur.fetchone()[0]

        cur.execute('SELECT COUNT(*) FROM files WHERE needs_analysis = 0')
        analyzed = cur.fetchone()[0]

        cur.execute('SELECT COUNT(*) FROM photo_features')
        features = cur.fetchone()[0]

        print(f"DB total={total}, analyzed={analyzed}, pending={pending}, features={features}")
    finally:
        conn.close()