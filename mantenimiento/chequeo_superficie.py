"""Surface test tipo Partition Wizard: lee el volumen en bloques grandes y
SECUENCIALES (no archivo por archivo navegando el sistema de archivos, que
en un HDD implica saltar el cabezal entre ubicaciones dispersas y es lo que
hacia lento a chequeo_lectura_completa.py). Mucho mas rapido, aunque no te
dice que archivo especifico esta en un bloque malo, solo el offset.

Resumible: guarda el offset ya leido en un checkpoint, asi que se puede
parar (Ctrl+C, cerrar la terminal) y retomar exactamente ahi.

Necesita permisos de administrador (acceso a volumen crudo).

    python chequeo_superficie.py E:
"""

import sys
from pathlib import Path as _Path

# Este script vive en mantenimiento/ pero los modulos (db.py, utils_*) y los
# datos (photos_v2.db, _salidas_locales/) viven en la raiz del repo.
_ROOT = _Path(__file__).resolve().parent.parent
_HERE = _Path(__file__).resolve().parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import sys
from pathlib import Path

BLOQUE = 8 * 1024 * 1024  # 8MB por lectura


def main():
    if len(sys.argv) < 2:
        print("uso: python chequeo_superficie.py <letra_unidad>: (ej: E:)")
        return
    letra = sys.argv[1].rstrip(":")
    ruta_volumen = rf"\\.\{letra}:"
    checkpoint = Path(f"chequeo_superficie_{letra}.offset")
    errores_log = Path(f"chequeo_superficie_{letra}_errores.txt")

    import shutil
    total = shutil.disk_usage(f"{letra}:\\").total

    inicio = int(checkpoint.read_text()) if checkpoint.exists() else 0
    print(f"tamano del volumen: {total/1024**3:.1f} GB")
    print(f"arrancando desde offset {inicio/1024**3:.2f} GB", flush=True)

    try:
        f = open(ruta_volumen, "rb")
    except PermissionError:
        print("ERROR: necesita permisos de administrador (correr PowerShell como admin)")
        return
    f.seek(inicio)

    offset = inicio
    leidos_desde_log = 0
    errores = 0
    with open(errores_log, "a", encoding="utf-8") as errlog:
        while offset < total:
            try:
                datos = f.read(BLOQUE)
                if not datos:
                    break
                offset += len(datos)
            except OSError as e:
                errores += 1
                linea = f"offset {offset}: {e}"
                print("  ERROR:", linea, flush=True)
                errlog.write(linea + "\n")
                errlog.flush()
                # saltar el bloque problematico y seguir
                offset += BLOQUE
                try:
                    f.seek(offset)
                except OSError:
                    pass

            leidos_desde_log += BLOQUE
            if leidos_desde_log >= 200 * 1024 * 1024:  # cada 200MB
                checkpoint.write_text(str(offset))
                leidos_desde_log = 0
                pct = offset / total * 100
                print(f"  {offset/1024**3:.1f} / {total/1024**3:.1f} GB  "
                      f"({pct:.1f}%)  {errores} errores", flush=True)

    checkpoint.write_text(str(offset))
    f.close()
    print(f"listo. {errores} bloques con error (ver {errores_log})")


if __name__ == "__main__":
    main()
