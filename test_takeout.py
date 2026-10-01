"""
Chequeo del soporte para sidecars .json de Google Takeout.

Las fotos bajadas de Google Photos vienen SIN EXIF: la fecha real está en un
JSON al lado. Si esto falla, el clustering temporal agrupa todo por la fecha
de descarga y los eventos salen mal.

    python test_takeout.py
"""
import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from utils_images import find_takeout_sidecar, read_exif_info, read_takeout_sidecar
from utils_analisis import read_gps

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 2021-03-01 00:00:00 UTC
TS = 1614556800
ESPERADO = datetime.fromtimestamp(TS).isoformat()


def foto_sin_exif(path: Path):
    """cv2.imwrite no escribe EXIF: simula exactamente lo que entrega Takeout."""
    cv2.imwrite(str(path), np.full((60, 80, 3), 128, dtype=np.uint8))


def sidecar(path: Path, con_gps=True):
    data = {
        "title": path.name,
        "photoTakenTime": {"timestamp": str(TS)},
        "geoData": ({"latitude": -33.0472, "longitude": -71.6127}
                    if con_gps else {"latitude": 0.0, "longitude": 0.0}),
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="takeout_test_"))
    try:
        # --- variantes de nombre que usa Takeout ---
        casos = {
            "clasico": ("IMG_0001.jpg", "IMG_0001.jpg.json"),
            "2024+":   ("IMG_0002.jpg", "IMG_0002.jpg.supplemental-metadata.json"),
            "corto":   ("IMG_0003.jpg", "IMG_0003.json"),
        }
        for nombre, (foto, js) in casos.items():
            fp = tmp / foto
            foto_sin_exif(fp)
            sidecar(tmp / js)
            assert find_takeout_sidecar(str(fp)) is not None, f"no encontró sidecar '{nombre}'"
            fecha, _, _ = read_takeout_sidecar(str(fp))
            assert fecha == ESPERADO, f"[{nombre}] fecha {fecha}, esperaba {ESPERADO}"
        print(f"OK: encuentra las {len(casos)} variantes de nombre de sidecar")

        # --- read_exif_info debe usar el sidecar, NO el mtime ---
        fp = tmp / "IMG_0001.jpg"
        fecha, _ = read_exif_info(str(fp))
        assert fecha == ESPERADO, f"read_exif_info devolvió {fecha}, esperaba {ESPERADO}"
        assert not fecha.startswith(str(datetime.now().year)), \
            "usó la fecha de hoy: el sidecar no se está leyendo"
        print(f"OK: read_exif_info usa la fecha del sidecar ({fecha[:10]}), no el mtime")

        # --- GPS del sidecar ---
        lat, lon = read_gps(str(fp))
        assert lat is not None and abs(lat - (-33.0472)) < 0.001, f"lat={lat}"
        assert lon is not None and abs(lon - (-71.6127)) < 0.001, f"lon={lon}"
        print(f"OK: GPS del sidecar ({lat}, {lon})")

        # --- geoData en 0.0 significa "sin dato", no la isla nula ---
        sin_geo = tmp / "IMG_0009.jpg"
        foto_sin_exif(sin_geo)
        sidecar(tmp / "IMG_0009.jpg.json", con_gps=False)
        lat0, lon0 = read_gps(str(sin_geo))
        assert lat0 is None and lon0 is None, f"0.0 debe ser None, dio ({lat0}, {lon0})"
        print("OK: geoData 0.0/0.0 se trata como 'sin GPS'")

        # --- sin sidecar: cae al mtime, no explota ---
        huerfana = tmp / "sin_sidecar.jpg"
        foto_sin_exif(huerfana)
        assert find_takeout_sidecar(str(huerfana)) is None
        fecha_mtime, _ = read_exif_info(str(huerfana))
        assert fecha_mtime is not None, "sin sidecar debe caer al mtime"
        print("OK: sin sidecar cae al mtime sin fallar")

        # --- sidecar corrupto: no debe romper el indexado ---
        rota = tmp / "rota.jpg"
        foto_sin_exif(rota)
        (tmp / "rota.jpg.json").write_text("{esto no es json", encoding="utf-8")
        fecha_rota, _ = read_exif_info(str(rota))
        assert fecha_rota is not None, "un JSON corrupto no debe dejar la foto sin fecha"
        print("OK: sidecar corrupto degrada al mtime sin romper")

        print("\n✅ SOPORTE DE GOOGLE TAKEOUT VERIFICADO")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
