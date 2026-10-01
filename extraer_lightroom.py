r"""
Descomprime los ZIP de la biblioteca de Lightroom a una sola carpeta.

Todos traen el mismo árbol (AÑO/AÑO-MM-DD/archivo), así que se fusionan en un
destino común. Dos zips pueden cubrir el mismo día, y ahí está el cuidado
principal: si un archivo ya existe con OTRO tamaño, NO se pisa -- se guarda al
lado como nombre__dup2.ext y queda anotado. Sobrescribir en silencio sería
perder una foto sin enterarse.

Solo toca los .zip completos. Los .crdownload son descargas a medio bajar y se
ignoran solos por la extensión.

    python extraer_lightroom.py                  # extrae lo que falte
    python extraer_lightroom.py --simular
    python extraer_lightroom.py --verificar      # solo chequea que los zip esten sanos

No borra los zip: eso lo decidís vos después de comprobar el contenido.
Es reanudable: cada zip terminado deja una marca y no se vuelve a abrir.
"""
import argparse
import csv
import os
import re
import shutil
import sys
import time
import zipfile

BUF = int(os.environ.get("EXTRAER_BUF_MB", 8)) << 20   # bloque de copia

ORIGEN = r"K:\Descargas"
DESTINO = r"K:\Lightroom_extraido"
MARCAS = os.path.join(DESTINO, "_extraidos")
LOG = r"C:\Renzo\Proyectos\IMGorgni\extraccion_lightroom.csv"


def numero(nombre):
    """Para ordenar _2 antes que _100, que por texto irían al revés."""
    m = re.search(r"_(\d+)\.zip$", nombre, re.I)
    return (int(m.group(1)) if m else 10**9, nombre.lower())


def destino_libre(ruta, tam):
    """Ruta donde escribir. Devuelve (ruta, estado):
       - 'nuevo'  no existía
       - 'igual'  ya está con el mismo tamaño: no hay nada que hacer
       - 'choque' existe con otro tamaño: se escribe al lado, no se pisa
    """
    if not os.path.exists(ruta):
        return ruta, "nuevo"
    if os.path.getsize(ruta) == tam:
        return ruta, "igual"
    raiz, ext = os.path.splitext(ruta)
    for i in range(2, 100):
        alt = f"{raiz}__dup{i}{ext}"
        if not os.path.exists(alt):
            return alt, "choque"
        if os.path.getsize(alt) == tam:
            return alt, "igual"
    return ruta, "igual"


def extraer(zf, destino, wlog, nombre_zip):
    """Devuelve (escritos, iguales, choques, bytes)."""
    esc = ig = ch = 0
    n_bytes = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        salida = os.path.join(destino, *info.filename.split("/"))
        ruta, estado = destino_libre(salida, info.file_size)
        if estado == "igual":
            ig += 1
            continue
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        # Los zips vienen STORED (sin comprimir): extraer es copiar bytes, no
        # descomprimir. El unico costo es E/S, y lectura y escritura van por el
        # mismo cable del dock, asi que conviene mover bloques grandes y hacer
        # menos llamadas al sistema.
        with zf.open(info) as src, open(ruta, "wb", BUF) as dst:
            shutil.copyfileobj(src, dst, BUF)
        n_bytes += info.file_size
        if estado == "choque":
            ch += 1
            wlog.writerow([time.strftime("%H:%M:%S"), nombre_zip, "CHOQUE",
                           info.filename, ruta])
        else:
            esc += 1
    return esc, ig, ch, n_bytes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--origen", default=ORIGEN)
    ap.add_argument("--destino", default=DESTINO)
    ap.add_argument("--simular", action="store_true")
    ap.add_argument("--verificar", action="store_true",
                    help="solo comprobar que los zip no estén corruptos")
    args = ap.parse_args()

    zips = sorted((f for f in os.listdir(args.origen) if f.lower().endswith(".zip")),
                  key=numero)
    marcas = os.path.join(args.destino, "_extraidos")
    os.makedirs(marcas, exist_ok=True)
    hechos = {f for f in os.listdir(marcas)}
    pend = [z for z in zips if z + ".ok" not in hechos]
    tam = sum(os.path.getsize(os.path.join(args.origen, z)) for z in pend)
    print(f"zips en {args.origen}: {len(zips):,}")
    print(f"  ya extraídos : {len(zips)-len(pend):,}")
    print(f"  pendientes   : {len(pend):,}  ({tam/1024**3:,.1f} GB comprimidos)")
    print(f"destino: {args.destino}\n")
    if args.simular or not pend:
        for z in pend[:10]:
            print("  " + z)
        return

    nuevo = not os.path.exists(LOG)
    flog = open(LOG, "a", encoding="utf-8", newline="")
    wlog = csv.writer(flog)
    if nuevo:
        wlog.writerow(["hora", "zip", "evento", "detalle", "extra"])

    t0 = time.time()
    tot_esc = tot_ig = tot_ch = tot_b = 0
    malos = 0
    for i, z in enumerate(pend, 1):
        ruta = os.path.join(args.origen, z)
        try:
            with zipfile.ZipFile(ruta) as zf:
                if args.verificar:
                    roto = zf.testzip()
                    estado = "SANO" if roto is None else f"CORRUPTO en {roto}"
                    print(f"  [{i}/{len(pend)}] {z}: {estado}")
                    wlog.writerow([time.strftime("%H:%M:%S"), z, estado, "", ""])
                    malos += roto is not None
                    continue
                esc, ig, ch, nb = extraer(zf, args.destino, wlog, z)
        except (zipfile.BadZipFile, OSError) as e:
            malos += 1
            print(f"  [{i}/{len(pend)}] {z}: NO SE PUDO -- {type(e).__name__}: {e}")
            wlog.writerow([time.strftime("%H:%M:%S"), z, "ERROR",
                           f"{type(e).__name__}: {e}"[:200], ""])
            flog.flush()
            continue
        open(os.path.join(marcas, z + ".ok"), "w").close()
        tot_esc += esc; tot_ig += ig; tot_ch += ch; tot_b += nb
        seg = time.time() - t0
        resta = (len(pend) - i) * (seg / i) / 60
        print(f"  [{i}/{len(pend)}] {z}: {esc:,} nuevos, {ig:,} ya estaban"
              + (f", {ch} CHOQUES" if ch else "")
              + f"  ({tot_b/1024**3:.0f} GB, {tot_b/1024**2/max(seg,1):.0f} MB/s,"
                f" faltan ~{resta:.0f} min)")
        wlog.writerow([time.strftime("%H:%M:%S"), z, "ok",
                       f"{esc} nuevos / {ig} iguales / {ch} choques", nb])
        flog.flush()
    flog.close()

    print(f"\nterminado en {(time.time()-t0)/60:.0f} min")
    print(f"  archivos escritos : {tot_esc:,}  ({tot_b/1024**3:,.1f} GB)")
    print(f"  ya estaban        : {tot_ig:,}")
    print(f"  choques de nombre : {tot_ch:,}" + ("  <- revisalos en el log" if tot_ch else ""))
    if malos:
        print(f"  zips con problema : {malos:,}")
    print(f"  log: {LOG}")


if __name__ == "__main__":
    main()
