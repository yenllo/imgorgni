"""
Encadena, desatendido:
  1. espera a que termine el rescate de F: (PID que se le pase)
  2. baja lo que falta de gdrive:Organizar/GDrive_MiDriveYcomputadoras (~47 GB)
  3. corre el pipeline completo (correr_pipeline_completo.py)

    python cadena_final.py --pid 6656
"""
import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PY = sys.executable
LOG = Path("cadena_final.log")
REMOTO = "gdrive:Organizar/GDrive_MiDriveYcomputadoras"
DESTINO_GD = r"E:\GDrive_MiDriveYcomputadoras"


def log(msg):
    linea = f"[{datetime.now():%d/%m %H:%M:%S}] {msg}"
    print(linea, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(linea + "\n")


def vivo(pid):
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                       capture_output=True, text=True, errors="replace")
    return str(pid) in (r.stdout or "")


def esperar(pid):
    log(f"⏳ esperando a que termine el rescate (PID {pid})...")
    while vivo(pid):
        time.sleep(120)
    log("✓ rescate de F: terminado")


def bajar_drive():
    log(f"▶ descargando {REMOTO}  ->  {DESTINO_GD}")
    t0 = time.time()
    # --ignore-existing: no re-baja lo ya presente (57 GB ya locales).
    # Reintentos altos: la conexión se corta y no queremos perder la noche.
    cmd = ["rclone", "copy", REMOTO, DESTINO_GD,
           "--ignore-existing", "--transfers", "4", "--retries", "10",
           "--low-level-retries", "20", "--stats", "5m", "--stats-one-line"]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    Path("drive_descarga.log").write_text(
        (r.stdout or "") + "\n--- STDERR ---\n" + (r.stderr or ""), encoding="utf-8")
    dur = (time.time() - t0) / 60
    if r.returncode == 0:
        log(f"  ✓ descarga completa — {dur:.0f} min")
    else:
        log(f"  ⚠️ rclone código {r.returncode} tras {dur:.0f} min "
            f"(sigo igual: lo bajado sirve) — ver drive_descarga.log")


def pipeline():
    log("▶ pipeline completo (fases 1,1,2,5,3,3c,4)")
    t0 = time.time()
    r = subprocess.run([PY, "correr_pipeline_completo.py"],
                       capture_output=True, text=True, errors="replace")
    Path("cadena_pipeline.log").write_text(
        (r.stdout or "") + "\n--- STDERR ---\n" + (r.stderr or ""), encoding="utf-8")
    log(f"  {'✓' if r.returncode == 0 else '✗'} pipeline — {(time.time()-t0)/3600:.1f} h")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True, help="PID del rescate en curso")
    args = ap.parse_args()

    log("=" * 60)
    log("CADENA: rescate -> Drive -> pipeline completo")
    log("=" * 60)
    t0 = time.time()
    esperar(args.pid)
    bajar_drive()
    pipeline()
    log("=" * 60)
    log(f"TODO TERMINADO en {(time.time()-t0)/3600:.1f} h")
    log("  resultado: E:\\Fotos_Organizadas_IA   |   duplicados.csv")
    log("=" * 60)


if __name__ == "__main__":
    main()
