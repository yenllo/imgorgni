"""
Script para agregar tags YOLO a fotos existentes
Ejecutar DESPUÃ‰S de completar Fase 2
"""
import json
import sqlite3
from pathlib import Path
from tqdm import tqdm
import numpy as np
import cv2
from utils_images import load_image_array

# Cargar config
with open("Config_32gb.json", "r") as f:
    cfg = json.load(f)

# Cargar YOLO
import onnxruntime as ort
sess = ort.InferenceSession(
    "C:/Git/Organizador_Fotos/models/yolo11m.onnx",
    providers=['DmlExecutionProvider', 'CPUExecutionProvider']
)

COCO_CLASSES = [
    "person","bicycle","car","motorcycle","airplane","bus","train","truck","boat",
    "traffic light","fire hydrant","stop sign","parking meter","bench","bird",
    "cat","dog","horse","sheep","cow","elephant","bear","zebra","giraffe",
    "backpack","umbrella","handbag","tie","suitcase","frisbee","skis","snowboard",
    "sports ball","kite","baseball bat","baseball glove","skateboard","surfboard",
    "tennis racket","bottle","wine glass","cup","fork","knife","spoon","bowl",
    "banana","apple","sandwich","orange","broccoli","carrot","hot dog","pizza",
    "donut","cake","chair","couch","potted plant","bed","dining table","toilet",
    "tv","laptop","mouse","remote","keyboard","cell phone","microwave","oven",
    "toaster","sink","refrigerator","book","clock","vase","scissors","teddy bear",
    "hair drier","toothbrush"
]

def preprocess(img):
    img = cv2.resize(img, (640, 640))
    img = img.astype(np.float32) / 255.0
    img = np.transpose(img, (2, 0, 1))
    return np.expand_dims(img, 0)

def postprocess(output, conf_th=0.25):
    tags = set()
    arr = output[0]
    if arr.ndim == 3 and arr.shape[2] >= 6:
        dets = arr[0]
        confs = dets[:, 4]
        valid = dets[confs >= conf_th]
        for det in valid:
            ci = int(np.argmax(det[5:]))
            if 0 <= ci < len(COCO_CLASSES):
                tags.add(COCO_CLASSES[ci])
    return sorted(list(tags))

# Conectar BD
conn = sqlite3.connect(cfg["db_path"])
conn.row_factory = sqlite3.Row

# Obtener fotos sin tags
rows = conn.execute("""
    SELECT f.id, f.file_path 
    FROM files f
    LEFT JOIN photo_features pf ON f.id = pf.photo_id
    WHERE pf.tags IS NULL OR pf.tags = '[]'
    LIMIT 5000
""").fetchall()

print(f"ðŸ“· Procesando {len(rows)} fotos...")

for row in tqdm(rows):
    try:
        img = load_image_array(row["file_path"])
        x = preprocess(img)
        out = sess.run(None, {sess.get_inputs()[0].name: x})
        tags = postprocess(out)
        
        if tags:
            tags_json = json.dumps(tags)
            conn.execute(
                "UPDATE photo_features SET tags = ? WHERE photo_id = ?",
                (tags_json, row["id"])
            )
    except:
        pass

conn.commit()
conn.close()
print("âœ… Tags agregados")
