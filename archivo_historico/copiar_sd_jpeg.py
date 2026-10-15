r"""Copia la SD de JPEG 2026 al 8TB y verifica cada archivo contra su hash.

Estas 24.648 fotos son COPIA UNICA: la tarjeta ya se vacio, los RAW de sesion
completa de F1 a F7 no existen, y en la biblioteca solo estan las exportaciones
de 3000x2004. Por eso la copia se verifica archivo por archivo contra
`manifiesto_dcim_jpeg_2026.tsv` en vez de confiar en que el copiado "anduvo":
una copia grande que se corta a la mitad no avisa.

    python copiar_sd_jpeg.py              # copia y verifica
    python copiar_sd_jpeg.py --verificar  # solo verifica lo ya copiado
    python copiar_sd_jpeg.py --destino K:\OtraCarpeta

Es reanudable: lo que ya esta copiado y con el hash correcto se saltea, asi que
si se corta a los 80 GB se vuelve a correr y sigue donde quedo.
"""
import argparse
import hashlib
import io
import os
import shutil
import subprocess
import sys
import time

ORIGEN = r"C:\Renzo\Fotografia\DCIM Jpeg 2026"
MANIFIESTO = r"C:\Renzo\Proyectos\IMGorgni\manifiesto_dcim_jpeg_2026.tsv"
DESTINO = r"K:\SD_Jpeg_2026"


def sha256(p, bloque=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        while True:
            b = fh.read(bloque)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def leer_manifiesto():
    filas = []
    for l in io.open(MANIFIESTO, encoding="utf-8").read().splitlines()[1:]:
        rel, by, sha, estado = l.split("\t")
        filas.append((rel, int(by), sha, estado))
    return filas


def avisar_si_k_es_red():
    """K: mapeado por red a la Pi le gana al dock local y la copia se iria por
    Tailscale a 1-2 MB/s (mas de un dia). Se saca con `net use K: /delete`."""
    try:
        s = subprocess.run(["net", "use"], capture_output=True, text=True,
                           timeout=15).stdout
    except Exception:
        return
    for l in s.splitlines():
        if "K:" in l and "\\\\" in l:
            print("!! K: esta mapeado por RED:", l.strip())
            print("!! El dock local no se va a ver hasta que corras:  net use K: /delete")
            print()
            return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--destino", default=DESTINO)
    ap.add_argument("--verificar", action="store_true",
                    help="no copia nada, solo chequea lo que ya esta en destino")
    ap.add_argument("--sin-verificar", action="store_true", dest="sin_verificar",
                    help="copia sin comparar el hash en el momento; hay que correr"
                         " despues --verificar, si no la copia queda sin comprobar")
    a = ap.parse_args()

    avisar_si_k_es_red()
    filas = leer_manifiesto()
    total_bytes = sum(b for _, b, _, _ in filas)
    print("origen  :", ORIGEN)
    print("destino :", a.destino)
    print("archivos: %d  (%.1f GB)" % (len(filas), total_bytes / 1e9))

    raiz_dest = os.path.splitdrive(a.destino)[0] or a.destino
    if not os.path.exists(raiz_dest + os.sep):
        sys.exit("no veo %s — ¿esta conectado el disco?" % raiz_dest)
    if not a.verificar:
        libre = shutil.disk_usage(raiz_dest + os.sep).free
        print("libre en %s: %.1f GB" % (raiz_dest, libre / 1e9))
        if libre < total_bytes * 1.05:
            sys.exit("no entra: hacen falta %.1f GB" % (total_bytes * 1.05 / 1e9))

    t0 = time.time()
    copiados = saltados = fallados = 0
    hechos_bytes = 0
    malos = []
    for i, (rel, by, sha, _e) in enumerate(filas):
        src = os.path.join(ORIGEN, rel)
        dst = os.path.join(a.destino, rel)
        # Ya estaba y no hay que volver a copiarlo. En modo normal se exige que
        # el contenido coincida; sin verificar alcanza con el tamaño, que es lo
        # unico que se puede saber sin leer el archivo.
        ya = os.path.exists(dst) and os.path.getsize(dst) == by
        if ya and (a.sin_verificar or sha256(dst) == sha):
            saltados += 1
            hechos_bytes += by
        elif a.verificar:
            fallados += 1
            malos.append(rel)
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            if a.sin_verificar:
                copiados += 1
            elif sha256(dst) != sha:         # la unica prueba que vale
                fallados += 1
                malos.append(rel)
            else:
                copiados += 1
            hechos_bytes += by
        if (i + 1) % 500 == 0:
            dt = time.time() - t0
            v = hechos_bytes / 1e6 / dt if dt else 0
            queda = (total_bytes - hechos_bytes) / 1e6 / v / 60 if v else 0
            print("   %5d/%d  %5.1f GB  %4.0f MB/s  faltan ~%.0f min"
                  % (i + 1, len(filas), hechos_bytes / 1e9, v, queda))

    dt = time.time() - t0
    print()
    print("copiados %d | ya estaban y coinciden %d | FALLADOS %d | %.0f s"
          % (copiados, saltados, fallados, dt))
    if malos:
        print()
        print("=== no coinciden con su hash (volver a correr para reintentar) ===")
        for m in malos[:30]:
            print("   ", m)
        sys.exit(1)
    if a.sin_verificar:
        print()
        print("OJO: se copio SIN comprobar el contenido. Para cerrar el circulo:")
        print("     python copiar_sd_jpeg.py --verificar")
    else:
        print("todos los %d archivos verificados contra su SHA256." % len(filas))


if __name__ == "__main__":
    main()
