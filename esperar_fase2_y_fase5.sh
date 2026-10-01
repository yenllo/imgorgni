#!/bin/bash
# Espera a que termine la Fase 2 (analisis IA) que ya esta corriendo sobre los
# 35.598 archivos que trajo OneDrive anoche, y encadena la Fase 5 para que
# detecte SIMILAR/RECOMPRIMIDO de ese material (ya tiene pHash) y los sume a
# duplicados.csv, que el curador reimporta solo.
#
# Se espera por needs_analysis en la base, no por el proceso: es la señal
# real de que terminó, sin importar cuantos workers use main.py por dentro.
cd /c/Renzo/Proyectos/IMGorgni || exit 1
hora() { date '+%H:%M:%S'; }

echo "===== $(hora) esperando a que termine la Fase 2 ====="
prev=-1; quietos=0
while true; do
  pend=$(python -c "
import sqlite3
c = sqlite3.connect('file:photos_v2.db?mode=ro', uri=True)
print(c.execute(\"SELECT COUNT(*) FROM files WHERE needs_analysis=1 AND COALESCE(borrado,0)=0\").fetchone()[0])
" 2>/dev/null)
  [ "$pend" = "0" ] && { echo "[$(hora)] Fase 2 terminada (0 pendientes)"; break; }
  if [ "$pend" = "$prev" ]; then
    quietos=$((quietos+1))
    [ $quietos -ge 20 ] && { echo "[$(hora)] sin avance 20 min ($pend pendientes), sigo igual"; break; }
  else
    quietos=0; prev=$pend
    echo "[$(hora)] pendientes: $pend"
  fi
  sleep 60
done

echo ""
echo "===== $(hora) Fase 5: detectando duplicados con todo el material nuevo ====="
python 5_duplicados.py -c config_K.json --csv duplicados.csv 2>&1 | tail -25

echo ""
echo "===== $(hora) LISTO ====="
echo "el curador reimporta duplicados.csv solo al detectar que cambio el mtime"
