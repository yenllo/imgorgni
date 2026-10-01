#!/bin/bash
# Bootstrap del droplet de DigitalOcean.
# Deja todo listo para procesar: dependencias, modelos, rclone y los datos.
#
#   bash setup_droplet.sh
#
# Idempotente: si algo falla, volvé a correrlo y retoma lo que falta.
set -euo pipefail

PROYECTO="${PROYECTO:-/root/IMGorgni}"
DATOS="${DATOS:-/mnt/fotos}"

log() { echo -e "\n\033[1;36m=== $* ===\033[0m"; }

log "1/6 Paquetes del sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-pip python3-venv tmux unzip libgl1 libglib2.0-0 curl

log "2/6 rclone"
if ! command -v rclone >/dev/null 2>&1; then
    curl -fsSL https://rclone.org/install.sh | bash
fi
rclone version | head -1

log "3/6 Dependencias de Python"
cd "$PROYECTO"
pip3 install --quiet --upgrade pip
pip3 install --quiet -r requirements_cloud.txt

log "4/6 Verificando GPU"
if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
else
    echo "AVISO: sin nvidia-smi. La Fase 2 va a correr en CPU (mucho mas lenta)."
fi
python3 - <<'PY'
import onnxruntime as ort
provs = ort.get_available_providers()
print("Providers ONNX:", provs)
if "CUDAExecutionProvider" not in provs:
    print("AVISO: sin CUDA. Revisa que este instalado onnxruntime-gpu y los drivers.")
PY

log "5/6 Modelos"
mkdir -p "$PROYECTO/models"
if [ ! -f "$PROYECTO/models/visual/model.onnx" ]; then
    python3 - <<'PY'
from huggingface_hub import hf_hub_download
p = hf_hub_download(repo_id="immich-app/ViT-B-32__openai",
                    filename="visual/model.onnx", local_dir="models")
print("CLIP visual:", p)
PY
fi
[ -f "$PROYECTO/models/yolov8n.onnx" ] || python3 setup_models.py --model_dir "$PROYECTO/models" --skip_clip || true
# scene_embeddings.npz viene del repo local (ya afinado); si falta, se regenera
[ -f "$PROYECTO/scene_embeddings.npz" ] || python3 precompute_scene_embeddings.py --model_dir "$PROYECTO/models"

log "6/6 Configuracion"
mkdir -p "$DATOS"
cp -n "$PROYECTO/config_cloud.json" "$PROYECTO/config.json" 2>/dev/null || true
python3 - <<PY
import json, pathlib
p = pathlib.Path("$PROYECTO/config.json")
cfg = json.loads(p.read_text())
cfg["root_dir"] = "$DATOS"
cfg["db_path"] = "$DATOS/photos.db"
cfg["scene_embeddings_path"] = "$PROYECTO/scene_embeddings.npz"
cfg["feature_model_onnx_path"] = "$PROYECTO/models/visual/model.onnx"
if not pathlib.Path(cfg.get("yolo_onnx_path","")).exists():
    cfg["enable_yolo"] = False
p.write_text(json.dumps(cfg, indent=2))
print("config.json listo:", cfg["root_dir"], "->", cfg["db_path"])
PY

log "LISTO"
cat <<'EOF'
Siguiente paso: traer las fotos desde Google Drive.

  rclone copy gdrive:Organizar /mnt/fotos --transfers 16 --checkers 32 --progress --fast-list

(Bajar de Drive NO consume el limite de 750 GB/dia, que es solo de subida.)

Despues, dentro de tmux:

  tmux new -s fotos
  python3 main.py -ph 1     # indexado
  python3 main.py -ph 2     # analisis IA (GPU)

Las fases 3, 4 y 5 se corren al final, cuando esten TODAS las fotos.
EOF
