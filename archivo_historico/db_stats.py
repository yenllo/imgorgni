import json, sqlite3, os
from pathlib import Path

cfg = json.load(open('config.json','r',encoding='utf-8'))
root = Path(cfg['root_dir'])
dbp = cfg['db_path']
discards = {cfg.get('discard_watermark_dir','_descartadas_watermark'), cfg.get('discard_mobile_dir','_descartadas_movil_screenshot'), cfg.get('discard_duplicates_dir','_duplicados_contenido')}

RAW_EXTS = {'.cr2','.arw','.nef','.rw2','.raf','.raw','.dng'}
IMG_EXTS = {'.jpg','.jpeg','.png','.gif','.tif','.tiff'} | RAW_EXTS

def walk_count(base: Path):
    exts = {e:0 for e in sorted(IMG_EXTS)}
    total = 0
    for b, ds, fs in os.walk(base):
        # skip discard dirs
        if any(p in Path(b).parts for p in discards):
            continue
        for f in fs:
            total += 1
            ext = Path(f).suffix.lower()
            if ext in exts:
                exts[ext] += 1
    return total, exts

T, exts = walk_count(root)
conn = sqlite3.connect(dbp)
cur = conn.cursor()
files_total = cur.execute('SELECT COUNT(*) FROM files').fetchone()[0]
needs0 = cur.execute('SELECT COUNT(*) FROM files WHERE needs_analysis=0').fetchone()[0]
needs1 = cur.execute('SELECT COUNT(*) FROM files WHERE needs_analysis=1').fetchone()[0]
features = cur.execute('SELECT COUNT(*) FROM photo_features').fetchone()[0]
print('\n=== Estado de disco vs BD ===')
print('Carpeta fotos (sin descarte) total archivos:', T)
print('Por extensiÃ³n:', exts)
print('BD files total:', files_total, '| analizados (needs_analysis=0):', needs0, '| pendientes:', needs1, '| features:', features)

print('\n=== Conteo en carpetas de descarte ===')
for d in sorted(discards):
    p = root / d
    cnt = 0
    for b, ds, fs in os.walk(p):
        cnt += len(fs)
    print(f'{d}: {cnt}')
conn.close()