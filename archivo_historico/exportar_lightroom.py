"""Extrae los zips de Lightroom (K:\\Descargas\\LightroomLibrary_*.zip) a
K:\\LightroomExport\\, usando C:\\LightroomStaging (SSD interno) como escala
intermedia.

K: es un HDD mecanico por USB: leer el zip y escribir el resultado en el
mismo disco lo obliga a saltar el cabezal entre las dos posiciones todo el
tiempo. Extrayendo primero a C: (disco fisico distinto, sin esa pelea) y
despues moviendo el lote ya extraido a K: de una sola pasada (transferencia
secuencial, mas rapido que la extraccion intercalada) sale mas rapido en
conjunto. El zip se borra apenas se extrae y verifica (no despues de
moverlo), asi K: se va liberando incluso si el lote todavia esta en C:.

(Antes usaba F:, que quedo con sectores dañados -- se cambio a K:, el 8TB
nuevo.)

Resumible: si el proceso muere a mitad de camino, la proxima corrida primero
vuelca a K: lo que haya quedado pendiente en el staging, y despues sigue con
los zips que falten (los ya movidos a K:\\LightroomExport no se re-procesan).

    python exportar_lightroom.py
"""
import shutil
import sys
import zipfile
from pathlib import Path

ORIGEN = Path(r"K:\Descargas")
STAGING = Path(r"C:\LightroomStaging")
DESTINO = Path(r"K:\LightroomExport")
LIBRE_MINIMO_GB = 10  # cuando C: baja de esto, se vuelca el lote a K:


def espacio_libre_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / (1024 ** 3)


def volcar_lote():
    """Mueve todo lo que haya en STAGING a DESTINO (transferencia grande y
    secuencial hacia el HDD, en vez de muchas escrituras chicas intercaladas
    con lecturas del zip)."""
    if not STAGING.exists():
        return
    carpetas = [p for p in STAGING.iterdir() if p.is_dir()]
    if not carpetas:
        return
    DESTINO.mkdir(parents=True, exist_ok=True)
    print(f"  volcando lote de {len(carpetas)} carpetas a F:...", flush=True)
    for carpeta in carpetas:
        destino_final = DESTINO / carpeta.name
        if destino_final.exists():
            shutil.rmtree(carpeta)  # ya estaba, era basura de una corrida anterior
        else:
            shutil.move(str(carpeta), str(destino_final))
    print(f"  lote volcado", flush=True)


def main():
    STAGING.mkdir(parents=True, exist_ok=True)
    DESTINO.mkdir(parents=True, exist_ok=True)

    volcar_lote()  # por si quedo algo pendiente de una corrida anterior

    zips = sorted(ORIGEN.glob("LightroomLibrary_*.zip"))
    ya_hechos = {p.name for p in DESTINO.iterdir() if p.is_dir()}
    pendientes = [z for z in zips if z.stem not in ya_hechos]
    print(f"{len(pendientes)} zips a procesar ({len(ya_hechos)} ya hechos)")

    for i, zp in enumerate(pendientes, 1):
        carpeta = STAGING / zp.stem
        if carpeta.exists():
            print(f"[{i}/{len(pendientes)}] {zp.name} ya en staging, salteo extraccion")
        else:
            print(f"[{i}/{len(pendientes)}] {zp.name} extrayendo a C:...", flush=True)
            tmp = carpeta.with_name(carpeta.name + ".tmp")
            if tmp.exists():
                shutil.rmtree(tmp)
            try:
                with zipfile.ZipFile(zp) as z:
                    entradas = [n for n in z.namelist() if not n.endswith("/")]
                    z.extractall(tmp)
            except Exception as e:
                print(f"  ERROR abriendo/extrayendo {zp.name}: {e}", file=sys.stderr)
                continue

            # Windows es case-insensitive: si el zip trae dos entradas que solo
            # difieren en mayus/minus (common en zips armados en Linux/Mac), la
            # segunda pisa a la primera al extraer y el conteo esperado real es
            # el de nombres unicos sin distinguir mayusculas, no el de entradas.
            esperados = len({n.lower() for n in entradas})
            extraidos = sum(1 for _ in tmp.rglob("*") if _.is_file())
            if extraidos != esperados:
                print(f"  MISMATCH {zp.name}: zip tiene {len(entradas)} entradas "
                      f"({esperados} nombres unicos), se extrajeron {extraidos}. "
                      f"No se borra el zip.", file=sys.stderr)
                continue
            tmp.rename(carpeta)

        zp.unlink()
        print(f"  ok: extraido y zip borrado", flush=True)

        if espacio_libre_gb(STAGING) < LIBRE_MINIMO_GB:
            volcar_lote()

    volcar_lote()  # lo que quede al final
    print("listo")


if __name__ == "__main__":
    main()
