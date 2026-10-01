# Correr desde la RAIZ de IMGorgni. El venv vive en C:\Renzo\Proyectos\Autocull\venv (no se mueve);
# para uno nuevo: python -m venv <ruta>; & <ruta>\Scripts\pip install -r autocull\requirements.txt ultralytics
$AC = "C:\Renzo\Proyectos\Autocull\venv\Scripts\python.exe"

# Both ONNX models must exist in models/ (raiz del proyecto) or the workers fail to initialize.
# Both are "s", not nano — measured on a real night roll:
#   yolov8s      — ball (+ persons, unused). Ball anchors protagonist selection;
#                  nano finds it in only 39% of frames vs 51% for s.
#   yolov8s-pose — persons + keypoints. These ARE the candidate pool for the
#                  protagonist, so a player nano misses can never be picked.
#                  s-pose finds 14% more players than nano.
New-Item -ItemType Directory -Force -Path models
& $AC -c "from ultralytics import YOLO; YOLO('yolov8s.pt').export(format='onnx')"
& $AC -c "from ultralytics import YOLO; YOLO('yolov8s-pose.pt').export(format='onnx')"
Move-Item -Path yolov8s.onnx -Destination models\yolov8s.onnx -Force
Move-Item -Path yolov8s-pose.onnx -Destination models\yolov8s-pose.onnx -Force
