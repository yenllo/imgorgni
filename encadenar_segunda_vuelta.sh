#!/bin/bash
# Espera a que termine la Fase 5 que ya esta corriendo (pid 55788) antes de
# lanzar la segunda vuelta: dos 5_duplicados.py a la vez escribirian el mismo
# duplicados.csv y se pisarian.
cd /c/Renzo/Proyectos/IMGorgni || exit 1
hora() { date '+%H:%M:%S'; }

echo "===== $(hora) esperando a que termine la Fase 5 en curso (pid 55788) ====="
while powershell -c "if(Get-Process -Id 55788 -ErrorAction SilentlyContinue){exit 0}else{exit 1}" 2>/dev/null; do
  sleep 20
done
echo "[$(hora)] Fase 5 (primera vuelta) termino"

bash esperar_fase2_final.sh
