#!/bin/bash
# Cadena desatendida de la noche.
#
# Extraccion y descarga van EN PARALELO (medido: competir cuesta ~4%, porque la
# extraccion es disco y la descarga es latencia de red).
#
# Y no se espera a las dos para empezar a procesar: apenas terminan los zips
# arranca el cruce, el indexado y la Fase 5 sobre Lightroom, que es lo que ya
# esta en disco. Asi los duplicados EXACTOS estan a mano ~3 h antes.
#
# Nada de aca borra ni mueve archivos: la Fase 1 corre con --no-mover y la
# Fase 5 solo escribe un CSV.
cd /c/Renzo/Proyectos/IMGorgni || exit 1
export EXTRAER_BUF_MB=16

hora() { date '+%H:%M:%S'; }
paso() { echo ""; echo "===== $(hora)  $1 ====="; }

# ---------------------------------------------------------------- descarga --
(
  for rama in otroPC Pictures Desktop Importaciones; do
    echo "[$(hora)] bajando $rama"
    python bajar_de_onedrive.py --ramas "$rama" --hilos 6 --log "bajada_$rama.csv" 2>&1 | tail -8
  done
  echo "[$(hora)] DESCARGA LISTA"
  touch .listo_descarga
) &
PID_DES=$!

# --------------------------------------------------------------- lightroom --
paso "A1 esperando a que terminen los zips"
# Se cuenta por MARCAS y no detectando el proceso: tasklist no muestra el
# nombre del script, y ese grep fallido llego a arrancar un segundo extractor.
total=$(ls /k/Descargas/*.zip 2>/dev/null | wc -l)
prev=-1; quietos=0
while true; do
  n=$(ls /k/Lightroom_extraido/_extraidos 2>/dev/null | wc -l)
  [ "$n" -ge "$total" ] && { echo "extraccion completa: $n/$total"; break; }
  if [ "$n" = "$prev" ]; then
    quietos=$((quietos+1)); [ $quietos -ge 15 ] && { echo "sin avance 15 min ($n/$total)"; break; }
  else quietos=0; prev=$n; fi
  sleep 60
done

paso "A2 segunda pasada de extraccion (zips que llegaron despues)"
python extraer_lightroom.py 2>&1 | tail -15
touch .listo_extraccion

paso "A3 cruzando Lightroom contra los dañados"
python cruzar_descarga.py "K:\\Lightroom_extraido" \
  --indice indice_lightroom.csv --prefijo lr_ 2>&1 | tail -25

paso "A4 Fase 1 sobre lo que ya hay (SIN mover nada)"
# Es incremental: saltea por ruta lo ya indexado, asi que correrla ahora y de
# nuevo al final no reprocesa nada. Toma Lightroom entero y la parte de
# OneDrive que haya bajado hasta este momento.
python main.py -ph 1 -c config_K.json --no-mover 2>&1 | tail -20

paso "A5 Fase 5: duplicados EXACTOS ya disponibles"
# Corre completa (no tiene modo "solo exactos"), pero los SIMILAR de las fotos
# nuevas todavia no salen porque su phash lo calcula la Fase 2.
python 5_duplicados.py -c config_K.json --csv duplicados.csv 2>&1 | tail -20
echo "[$(hora)] EXACTOS listos en el curador"

# ---------------------------------------------------------------- onedrive --
paso "B1 esperando a que termine la descarga"
wait $PID_DES

paso "B2 cruzando OneDrive contra los dañados"
python cruzar_descarga.py "K:\\OneDrive_bajado" \
  --indice indice_onedrive.csv --prefijo od_ 2>&1 | tail -25

paso "B3 Fase 1 sobre lo que bajo despues"
python main.py -ph 1 -c config_K.json --no-mover 2>&1 | tail -20

paso "B4 Fase 2: analisis IA de todo lo nuevo"
python main.py -ph 2 -c config_K.json 2>&1 | tail -25

paso "B5 Fase 5 final, ya con phash de todo"
# El curador importa sin pisar decisiones: solo agrega pares nuevos, asi que
# los 17.638 que venias revisando quedan intactos.
python 5_duplicados.py -c config_K.json --csv duplicados.csv 2>&1 | tail -20

paso "LISTO"
echo "resultados: lr_recuperables.csv  lr_siguen_faltando.csv  lr_material_nuevo.csv"
echo "            od_recuperables.csv  od_siguen_faltando.csv  od_material_nuevo.csv"
echo ""
echo "NO corrio, a proposito: Fase 3 (eventos) reagrupa los 548 que ya"
echo "ordenaste a mano, conviene mirarlo despierto."
