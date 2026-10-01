"""Verifica los zips de Lightroom en F:\\Descargas (el disco con sectores
dañados): separa cuales estan sanos (se pueden copiar tal cual a K:, sin
volver a bajarlos) de cuales estan corruptos (hay que re-descargar solo
esos, no los 800GB enteros de nuevo).

Usa zipfile.testzip(), que verifica el CRC de cada entrada del zip sin
necesidad de extraerlo.

Resumible: los resultados se van agregando a zips_sanos.txt /
zips_corruptos.txt a medida que se procesan, asi que si F: se cuelga a
mitad de camino (ya paso antes) no se pierde lo verificado.

    python verificar_zips_F.py
"""
import zipfile
from pathlib import Path

ORIGEN = Path(r"F:\Descargas")
SANOS = Path("zips_sanos.txt")
CORRUPTOS = Path("zips_corruptos.txt")


def ya_verificados() -> set:
    hechos = set()
    for f in (SANOS, CORRUPTOS):
        if f.exists():
            hechos |= set(f.read_text(encoding="utf-8", errors="ignore").splitlines())
    return hechos


def main():
    hechos = ya_verificados()
    zips = sorted(ORIGEN.glob("LightroomLibrary_*.zip"))
    pendientes = [z for z in zips if z.name not in hechos]
    print(f"{len(zips)} zips totales, {len(hechos)} ya verificados, "
          f"{len(pendientes)} pendientes", flush=True)

    with open(SANOS, "a", encoding="utf-8") as f_sanos, \
         open(CORRUPTOS, "a", encoding="utf-8") as f_corruptos:
        for i, zp in enumerate(pendientes, 1):
            try:
                with zipfile.ZipFile(zp) as z:
                    malo = z.testzip()
                if malo is None:
                    f_sanos.write(zp.name + "\n")
                    f_sanos.flush()
                    print(f"[{i}/{len(pendientes)}] OK        {zp.name}", flush=True)
                else:
                    f_corruptos.write(zp.name + "\n")
                    f_corruptos.flush()
                    print(f"[{i}/{len(pendientes)}] CORRUPTO ({malo}) {zp.name}", flush=True)
            except (zipfile.BadZipFile, OSError) as e:
                f_corruptos.write(zp.name + "\n")
                f_corruptos.flush()
                print(f"[{i}/{len(pendientes)}] CORRUPTO ({e}) {zp.name}", flush=True)

    print(f"\nlisto. ver {SANOS} y {CORRUPTOS}")


if __name__ == "__main__":
    main()
