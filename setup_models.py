import argparse
import json
import os
from pathlib import Path
import subprocess

import onnxruntime as ort
from huggingface_hub import hf_hub_download

YOLOR_EXT_SCRIPT_URL = "https://raw.githubusercontent.com/microsoft/onnxruntime-extensions/main/tutorials/yolo_e2e.py"


def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)


def download_clip_onnx(dest_dir: Path) -> Path:
    # Preferir FP16 por rendimiento y memoria
    repo_id = "sayantan47/clip-vit-b32-onnx"
    filenames = ["onnx/model_fp16.onnx", "onnx/model.onnx"]
    for fn in filenames:
        try:
            local_path = hf_hub_download(repo_id=repo_id, filename=fn)
            target = dest_dir / Path(fn).name
            Path(target).write_bytes(Path(local_path).read_bytes())
            return target
        except Exception:
            continue
    # Fallback a Immich repo si falla
    repo_id2 = "immich-app/ViT-B-32__openai"
    try:
        local_path = hf_hub_download(repo_id=repo_id2, filename="model.onnx")
        target = dest_dir / "clip_vit_b32.onnx"
        Path(target).write_bytes(Path(local_path).read_bytes())
        return target
    except Exception as e:
        raise RuntimeError(f"No se pudo descargar CLIP ONNX: {e}")


def export_yolov8_onnx(dest_dir: Path) -> Path:
    # Descarga y ejecuta el script de ORT Extensions que exporta yolov8n.onnx
    import requests
    import tempfile
    r = requests.get(YOLOR_EXT_SCRIPT_URL, timeout=30)
    r.raise_for_status()
    with tempfile.TemporaryDirectory() as td:
        script_path = Path(td) / "yolo_e2e.py"
        script_path.write_text(r.text, encoding="utf-8")
        # Ejecutar el script: generarÃ¡ yolov8n.onnx en el cwd
        subprocess.check_call(["python", str(script_path)])
        # Mover el archivo resultante si existe
        src_candidates = [Path(td) / "yolov8n.onnx", Path("yolov8n.onnx")]  # algunos scripts guardan en cwd
        for src in src_candidates:
            if src.exists():
                target = dest_dir / "yolov8n.onnx"
                target.write_bytes(src.read_bytes())
                return target
    raise RuntimeError("No se encontrÃ³ yolov8n.onnx tras la exportaciÃ³n")


def export_yolov8_ultralytics(dest_dir: Path) -> Path:
    try:
        from ultralytics import YOLO
    except Exception as e:
        raise RuntimeError(f"Ultralytics no disponible: {e}")
    model = YOLO('yolov8n.pt')
    onnx_path = model.export(format='onnx', imgsz=640)
    src = Path(onnx_path)
    target = dest_dir / src.name
    target.write_bytes(src.read_bytes())
    return target


def verify_directml(path: Path) -> list[str]:
    providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
    try:
        sess = ort.InferenceSession(str(path), providers=providers)
        return sess.get_providers()
    except Exception as e:
        return [f"ERROR: {e}"]


def main():
    ap = argparse.ArgumentParser(description="Setup de modelos ONNX (YOLOv8 y CLIP) y actualizaciÃ³n de config.json")
    ap.add_argument("--model_dir", type=str, default=str(Path("C:/Modelos")), help="Carpeta destino para modelos")
    ap.add_argument("--config", type=str, default="config.json", help="Ruta a config.json para actualizar")
    ap.add_argument("--skip_yolo", action="store_true", help="Saltar exportaciÃ³n de YOLOv8n")
    ap.add_argument("--skip_clip", action="store_true", help="Saltar descarga de CLIP")
    args = ap.parse_args()

    model_dir = Path(args.model_dir)
    ensure_dir(model_dir)

    yolo_path = None
    clip_path = None

    if not args.skip_yolo:
        try:
            yolo_path = export_yolov8_onnx(model_dir)
            print(f"YOLOv8 ONNX listo en: {yolo_path}")
            print("Providers:", verify_directml(yolo_path))
        except Exception as e:
            print(f"Advertencia: exportaciÃ³n con ORT Extensions fallÃ³: {e}")
            print("Intentando exportaciÃ³n con Ultralytics...")
            try:
                yolo_path = export_yolov8_ultralytics(model_dir)
                print(f"YOLOv8 ONNX listo en: {yolo_path}")
                print("Providers:", verify_directml(yolo_path))
            except Exception as e2:
                print(f"Error al exportar con Ultralytics: {e2}")
                print("Puedes instalar 'ultralytics' y exportar manualmente:")
                print("  pip install ultralytics")
                print("  python -c \"from ultralytics import YOLO; YOLO('yolov8n.pt').export(format='onnx', imgsz=640)\"")

    if not args.skip_clip:
        try:
            clip_path = download_clip_onnx(model_dir)
            print(f"CLIP ONNX listo en: {clip_path}")
            print("Providers:", verify_directml(clip_path))
        except Exception as e:
            print(f"Advertencia: no se pudo descargar CLIP ONNX: {e}")

    # Actualizar config.json si hay nuevos paths
    try:
        cfg_path = Path(args.config)
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        changed = False
        if yolo_path:
            data["yolo_onnx_path"] = str(yolo_path)
            changed = True
        if clip_path:
            data["feature_model_onnx_path"] = str(clip_path)
            changed = True
        if changed:
            cfg_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            print(f"config.json actualizado: {cfg_path}")
        else:
            print("Sin cambios en config.json")
    except Exception as e:
        print(f"No se pudo actualizar config.json: {e}")


if __name__ == "__main__":
    main()