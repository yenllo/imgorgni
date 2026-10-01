#!/bin/bash
# Segunda vuelta: la primera espera se rindio a los 20 min porque la Fase 2
# estaba atascada en algo pesado (proceso vivo, consumiendo CPU, solo lento),
# asi que la Fase 5 corrio con 18.318 fotos todavia sin pHash -- esas quedaron
# afuera de SIMILAR/RECOMPRIMIDO esta vuelta (EXACTO si es completo, solo
# necesita sha256).
#
# Esta vuelta espera SIN limite de tiempo hasta que needs_analysis llegue a 0
# de verdad, y recien ahi corre la Fase 5 final.
cd /c/Renzo/Proyectos/IMGorgni || exit 1
hora() { date '+%H:%M:%S'; }

echo "===== $(hora) esperando a que la Fase 2 termine DE VERDAD (sin timeout) ====="
prev=-1
while true; do
  pend=$(python -c "
import sqlite3
c = sqlite3.connect('file:photos_v2.db?mode=ro', uri=True)
print(c.execute(\"SELECT COUNT(*) FROM files WHERE needs_analysis=1 AND COALESCE(borrado,0)=0\").fetchone()[0])
" 2>/dev/null)
  [ "$pend" = "0" ] && { echo "[$(hora)] Fase 2 terminada (0 pendientes)"; break; }
  [ "$pend" != "$prev" ] && { echo "[$(hora)] pendientes: $pend"; prev=$pend; }
  sleep 60
done

echo ""
echo "===== $(hora) Fase 5 FINAL: con pHash de todo el material ====="
python 5_duplicados.py -c config_K.json --csv duplicados.csv 2>&1 | tail -25

echo ""
echo "===== $(hora) LISTO DE VERDAD ====="
