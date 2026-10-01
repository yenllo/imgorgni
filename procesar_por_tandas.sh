#!/bin/bash
# Procesa la fototeca por tandas en el disco local del droplet.
#
# Evita necesitar un volume de 1.5 TB: baja una carpeta de Drive, corre las
# fases 1 y 2 (que son las que necesitan GPU), borra las fotos y pasa a la
# siguiente. La base de datos queda con todo el análisis y pesa poco.
#
#   bash procesar_por_tandas.sh
#
# Reanudable: las fotos ya analizadas se saltean (needs_analysis=0), así que
# si se corta, volvés a correrlo y sigue.
set -uo pipefail

PROYECTO="${PROYECTO:-/root/IMGorgni}"
DATOS="${DATOS:-/mnt/fotos}"
LIBRE_MINIMO_GB="${LIBRE_MINIMO_GB:-60}"

# Carpetas de Drive a procesar, en orden. Las chicas primero para ver
# resultados rápido; los RAW pesados al final.
TANDAS=(
  "Organizar/HDD_E/E:"
  "Organizar/GDrive_MiDriveYcomputadoras"
  "Cosas mama"
  "ColegioYWeas"
  "Organizar/HDD_F/F:"
  "Organizar/Lightroom"
)

log() { echo -e "\n\033[1;36m[$(date +%H:%M:%S)] $*\033[0m"; }

cd "$PROYECTO"
mkdir -p "$DATOS"

for tanda in "${TANDAS[@]}"; do
    destino="$DATOS/$(echo "$tanda" | tr '/:' '__')"

    log "TANDA: $tanda"

    # ¿Existe en Drive? (Lightroom puede no estar subida todavía)
    if ! rclone lsd "gdrive:$tanda" >/dev/null 2>&1; then
        echo "  no existe en Drive todavia - salteando"
        continue
    fi

    tam=$(rclone size "gdrive:$tanda" --json 2>/dev/null | python3 -c "import json,sys; print(round(json.load(sys.stdin)['bytes']/1e9,1))" 2>/dev/null || echo "?")
    echo "  tamaño en Drive: ${tam} GB"

    log "Bajando desde Drive..."
    rclone copy "gdrive:$tanda" "$destino" \
        --transfers 16 --checkers 32 --fast-list \
        --stats 60s --stats-one-line

    libre=$(df -BG --output=avail "$DATOS" | tail -1 | tr -dc '0-9')
    echo "  espacio libre tras la bajada: ${libre} GB"

    log "Fase 1: indexado"
    python3 main.py -ph 1 || echo "  fase 1 termino con error, sigo"

    log "Fase 2: analisis IA (GPU)"
    python3 main.py -ph 2 || echo "  fase 2 termino con error, sigo"

    # Backup de la BD: es lo unico irreemplazable de esta tanda
    cp -f "$DATOS/photos.db" "$PROYECTO/photos.db.backup" 2>/dev/null || true

    log "Borrando fotos de la tanda (el analisis ya esta en la BD)"
    rm -rf "$destino"

    python3 - <<'PY'
import json, sqlite3, pathlib
cfg = json.loads(pathlib.Path("config.json").read_text())
c = sqlite3.connect(cfg["db_path"])
tot = c.execute("SELECT COUNT(*) FROM files").fetchone()[0]
ana = c.execute("SELECT COUNT(*) FROM photo_features").fetchone()[0]
pend = c.execute("SELECT COUNT(*) FROM files WHERE needs_analysis=1").fetchone()[0]
print(f"  ACUMULADO: {tot:,} indexadas | {ana:,} analizadas | {pend:,} pendientes")
c.close()
PY
done

log "TODAS LAS TANDAS COMPLETADAS"
echo "Bajá la base de datos a tu PC:"
echo "  scp root@<IP>:$DATOS/photos.db C:\\Renzo\\Proyectos\\IMGorgni\\photos_nube.db"
echo ""
echo "Y despues, localmente:"
echo "  python remapear_rutas.py photos_nube.db"
echo "  python main.py -ph 3 -c config_local.json"
echo "  python 5_duplicados.py -c config_local.json"
echo "  python 4_exportar.py -c config_local.json --out E:\\Fotos_Organizadas_IA"
