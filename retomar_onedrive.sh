#!/bin/bash
# Retoma lo que OneDrive corto anoche (WinError 389, 35.638 archivos) y despues
# rehace el cruce y el indexado, que la primera vez corrieron con datos a medias.
#
# 3 hilos en vez de 6: el corte tiene pinta de limite de tasa, y el script ahora
# reintenta cada archivo 3 veces y hace una pausa de 10 min si ve 25 fallos
# seguidos, en vez de quemar la lista entera como anoche.
cd /c/Renzo/Proyectos/IMGorgni || exit 1
hora() { date '+%H:%M:%S'; }
paso() { echo ""; echo "===== $(hora)  $1 ====="; }

for vuelta in 1 2; do
  paso "vuelta $vuelta de descarga"
  for rama in Pictures otroPC Desktop Importaciones; do
    echo "[$(hora)] $rama"
    python bajar_de_onedrive.py --ramas "$rama" --hilos 12 --log "bajada_$rama.csv" 2>&1 | tail -8
  done
done

paso "cruce OneDrive (ahora con todo bajado)"
python cruzar_descarga.py "K:\\OneDrive_bajado" \
  --indice indice_onedrive.csv --prefijo od_ 2>&1 | tail -25

paso "Fase 1 sobre lo que bajo (SIN mover nada)"
python main.py -ph 1 -c config_K.json --no-mover 2>&1 | tail -20

paso "LISTO"
echo "revisar od_recuperables.csv y od_material_nuevo.csv"
echo "la Fase 2 hay que volver a correrla para analizar esto ultimo."


