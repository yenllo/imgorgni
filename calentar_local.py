#!/usr/bin/env python3
"""Calienta las previews de una galeria EN LA NOTEBOOK y las sube a la Pi.

La Pi tarda ~1,5 s por foto y se recalienta (se reinicio el 13/9/2026 en pleno
calentado). La notebook tiene 16 nucleos y lee las fotos por la red.

Funciona porque la clave del cache es (ruta de la Pi, mtime_ns, parametros):
se calcula con la ruta /mnt/fotos/... aunque el archivo se lea desde Y:, y el
mtime_ns da identico en los dos lados (medido). Se usa el mismo galeria.py
(pi_galeria/galeria.py) y la misma fuente de la Pi para la marca de agua.

    python calentar_local.py <slug> --probar 5    # verifica claves, no sube nada
    python calentar_local.py <slug>               # calienta todo y sube

Antes de correrlo: python galeria_pi.py traer  (el galeria.py local al dia).
"""
import argparse
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import time
from multiprocessing import Pool
from pathlib import Path

AQUI = Path(__file__).resolve().parent
PI = "fotosnas"
PI_RAIZ = "/mnt/fotos/"
PI_GALERIA = "/home/yenllo/galeria"
# las derivadas viven en el SSD de la Pi y Samba no las comparte: se bajan por
# ssh a una copia local antes de calentar
PI_DERIVADAS = PI_GALERIA + "/derivadas/"
# el mismo disco montado en la notebook: Y: en casa (LAN), Z: por Tailscale.
# ponytail: por Tailscale se lee a ~2-5 MB/s; sirve para galerias chicas, una
# de 5000 fotos son horas. Si molesta, que la Pi mande el draft ya reducido.
RAICES = ("Y:/", "Z:/")


def raiz_local():
    """La primera raiz que muestra el disco de verdad (con Fotos/ adentro).
    Un share montado pero con el 8TB caido en la Pi se ve vacio: no cuenta."""
    for r in RAICES:
        try:
            if os.path.isdir(r + "Fotos"):
                return r
        except OSError:          # unidad de red caida: "ya no esta disponible"
            pass
    return None
FUENTE_PI = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
TRABAJO = AQUI / "_cache_local"


def ssh(cmd, **kw):
    return subprocess.run(["ssh", PI, cmd], text=True, capture_output=True, **kw)


def pi_python(codigo):
    """Corre codigo en la Pi, con galeria.py importable, y devuelve su JSON."""
    r = subprocess.run(["ssh", PI, "cd %s && python3 -" % PI_GALERIA],
                       input=codigo, text=True, capture_output=True)
    if r.returncode:
        sys.exit("fallo en la Pi:\n" + r.stderr[-1500:])
    return json.loads(r.stdout.strip().splitlines()[-1])


# ------------------------------------------------------------ en cada worker
RAIZ = DERIV = None
MTIMES = {}


def a_local(p):
    s = str(p)
    if s.startswith(PI_RAIZ):
        return RAIZ + s[len(PI_RAIZ):]
    if s.startswith(PI_DERIVADAS):
        return DERIV + "/" + s[len(PI_DERIVADAS):]
    return s


class _Stat:
    """stat local, pero con el mtime_ns que dio la Pi: la clave del cache sale
    de ahi, y una copia (o Samba) puede redondear los nanosegundos."""
    def __init__(self, real, ns):
        self._real, self.st_mtime_ns = real, ns

    def __getattr__(self, k):
        return getattr(self._real, k)


class _Os:
    """os de galeria.py, con stat() leyendo la ruta de la Pi en la notebook."""
    def __getattr__(self, k):
        return getattr(os, k)

    def stat(self, p, *a, **kw):
        real = os.stat(a_local(p), *a, **kw)
        ns = MTIMES.get(str(p))
        return _Stat(real, ns) if ns is not None else real


class _Image:
    """PIL.Image de galeria.py, con open() leyendo por Y: la ruta de la Pi."""
    def __init__(self, real):
        self._real = real

    def __getattr__(self, k):
        return getattr(self._real, k)

    def open(self, p, *a, **kw):
        return self._real.open(a_local(p), *a, **kw)


G = GAL = None


def iniciar(cache, fuente, logos, gal, raiz, deriv, mtimes):
    global G, GAL, RAIZ, DERIV, MTIMES
    RAIZ, DERIV, MTIMES = raiz, deriv, mtimes
    sys.path.insert(0, str(AQUI / "pi_galeria"))
    import galeria as modulo
    from PIL import Image, ImageFont
    modulo.os = _Os()
    modulo.Image = _Image(Image)
    modulo.CACHE = Path(cache)
    modulo.LOGOS = Path(logos)
    modulo._fuente = lambda px: ImageFont.truetype(fuente, px)
    modulo.capa_marca.cache_clear()
    G, GAL = modulo, gal


def uno(tarea):
    fid, ruta = tarea
    try:
        G.preview_de(GAL, fid, ruta)                    # grilla
        G.preview_de(GAL, fid, ruta, G.ANCHO_VISOR)     # visor
        G.sin_marca(ruta)                               # panel
        return None
    except Exception as e:
        return "%s: %s" % (ruta, e)


# ------------------------------------------------------------ principal
def datos(slug):
    return pi_python('''
import json, os, sqlite3
c = sqlite3.connect("galeria.db"); c.row_factory = sqlite3.Row
g = c.execute("SELECT * FROM galeria WHERE slug = ?", (%r,)).fetchone()
fotos = [[r["id"], r["ruta"]] for r in
         c.execute("SELECT id, ruta FROM foto WHERE galeria_id = ? ORDER BY orden", (g["id"],))]
mt = {}
for _i, r in fotos:
    try:
        mt[r] = os.stat(r).st_mtime_ns
    except OSError:
        pass
print(json.dumps({"gal": dict(g), "fotos": fotos, "mtimes": mt}))
''' % slug)


def claves_en_pi(slug, ids):
    """Las rutas de cache (relativas) que la Pi calcula para estas fotos."""
    return pi_python('''
import json, sqlite3, galeria as G
c = sqlite3.connect("galeria.db"); c.row_factory = sqlite3.Row
g = c.execute("SELECT * FROM galeria WHERE slug = ?", (%r,)).fetchone()
ids = %r
out = []
for r in c.execute("SELECT id, ruta FROM foto WHERE id IN (%%s)" %% ",".join("?"*len(ids)), ids):
    for p in (G.clave_preview_de(g, r["ruta"]), G.clave_preview_de(g, r["ruta"], G.ANCHO_VISOR),
              G._clave_sin_marca(r["ruta"])):
        out.append(str(p.relative_to(G.CACHE)))
print(json.dumps(sorted(set(out))))
''' % (slug, ids))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--probar", type=int, metavar="N",
                    help="solo N fotos, y compara las claves con las de la Pi")
    ap.add_argument("--procesos", type=int, default=max(2, (os.cpu_count() or 4) - 4))
    ap.add_argument("--raiz", metavar="DIR",
                    help="lee los originales de aca en vez de Y:/Z:. DIR tiene que "
                         "replicar la estructura de la Pi bajo /mnt/fotos/ (con "
                         "junctions alcanza). Para cuando el 8TB esta caido y las "
                         "fotos estan en la notebook (20/9/2026).")
    ap.add_argument("--derivadas", metavar="DIR",
                    help="las fotos que viven en derivadas/ de la Pi ya estan "
                         "aca: no las baja. DIR replica el arbol de derivadas/.")
    a = ap.parse_args()

    d = datos(a.slug)
    gal, fotos = d["gal"], [tuple(f) for f in d["fotos"]]
    if a.probar:
        fotos = fotos[:a.probar]
    raiz = (a.raiz.replace("\\", "/").rstrip("/") + "/") if a.raiz else raiz_local()
    if raiz is None and any(r.startswith(PI_RAIZ) for _i, r in fotos):
        sys.exit("no veo el disco de fotos en %s: ni LAN ni Tailscale" % " ni ".join(RAICES))
    print("galeria %r: %d fotos, marca=%s, disco por %s" %
          (gal["nombre"], len(fotos), gal["marca"], raiz))

    salida = TRABAJO / a.slug
    if salida.exists():
        shutil.rmtree(salida)
    salida.mkdir(parents=True)

    # fotos en las derivadas de la Pi: se bajan solo esos archivos, salvo que
    # --derivadas diga que ya estan en la notebook (20/9/2026: la galeria vive
    # en la SSD porque el 8TB se cayo, y bajar lo que acabamos de subir son
    # 20 min al pedo)
    deriv = pathlib.Path(a.derivadas) if a.derivadas else TRABAJO / ("_derivadas-" + a.slug)
    rels = [] if a.derivadas else [r[len(PI_DERIVADAS):] for _i, r in fotos
                                   if r.startswith(PI_DERIVADAS)]
    if rels:
        if deriv.exists():
            shutil.rmtree(deriv)
        deriv.mkdir(parents=True)
        print("  bajando %d derivadas de la Pi..." % len(rels))
        t = time.time()
        tar = subprocess.Popen(["ssh", PI, "tar cf - -C %s --verbatim-files-from -T -"
                                % shlex.quote(PI_DERIVADAS)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        ext = subprocess.Popen(["tar", "xf", "-", "-C", str(deriv)], stdin=tar.stdout)
        tar.stdin.write(("\n".join(rels) + "\n").encode())
        tar.stdin.close()
        tar.stdout.close()
        r = ext
        ext.wait()
        tar.wait()
        if r.returncode or tar.returncode:
            sys.exit("fallo la bajada de derivadas")
        print("  bajadas en %.0f s" % (time.time() - t))
    fuente = TRABAJO / "DejaVuSans-Bold.ttf"
    if not fuente.exists():
        subprocess.run(["scp", "-q", "%s:%s" % (PI, FUENTE_PI), str(fuente)], check=True)
    logos = TRABAJO / "logos"
    if logos.exists():
        shutil.rmtree(logos)
    subprocess.run(["scp", "-q", "-r", "%s:%s/logos" % (PI, PI_GALERIA), str(logos)])
    logos.mkdir(exist_ok=True)

    t0 = time.time()
    errores = []
    with Pool(a.procesos, initializer=iniciar,
              initargs=(str(salida), str(fuente), str(logos), gal, raiz,
                        deriv.as_posix(), d["mtimes"])) as pool:
        for i, err in enumerate(pool.imap_unordered(uno, fotos, chunksize=4), 1):
            if err:
                errores.append(err)
            if i % 50 == 0 or i == len(fotos):
                v = i / (time.time() - t0)
                print("\r  %d/%d  %.1f fotos/s  faltan %.0f s" %
                      (i, len(fotos), v, (len(fotos) - i) / v), end="", flush=True)
    print("\n  generado en %.0f s, %d errores" % (time.time() - t0, len(errores)))
    for e in errores[:5]:
        print("   ", e)

    locales = sorted(str(p.relative_to(salida)).replace("\\", "/")
                     for p in salida.rglob("*.jpg"))
    if a.probar:
        esperadas = claves_en_pi(a.slug, [f[0] for f in fotos])
        if locales == esperadas:
            print("CLAVES IDENTICAS a las de la Pi (%d archivos). Se puede correr completo."
                  % len(locales))
        else:
            print("NO COINCIDEN. solo local: %s  solo Pi: %s" %
                  (sorted(set(locales) - set(esperadas))[:3],
                   sorted(set(esperadas) - set(locales))[:3]))
        return

    mb = sum(p.stat().st_size for p in salida.rglob("*.jpg")) / 1e6
    print("  subiendo %d archivos (%.0f MB) a la Pi..." % (len(locales), mb))
    t1 = time.time()
    tar = subprocess.Popen(["tar", "cf", "-", "-C", str(salida), "."], stdout=subprocess.PIPE)
    r = subprocess.run(["ssh", PI, "tar xf - -C %s/cache" % PI_GALERIA], stdin=tar.stdout)
    tar.wait()
    if r.returncode or tar.returncode:
        sys.exit("fallo la subida; la carpeta local queda en %s" % salida)
    print("  subido en %.0f s" % (time.time() - t1))

    listas = pi_python('''
import json, sqlite3, galeria as G
c = sqlite3.connect("galeria.db"); c.row_factory = sqlite3.Row
g = c.execute("SELECT id FROM galeria WHERE slug = ?", (%r,)).fetchone()
print(json.dumps(G.calentadas_de(c, g["id"])))
''' % a.slug)
    print("  la Pi dice: %d/%d calentadas" % tuple(listas))
    if listas[0] != listas[1]:
        # incompleto (tipico: cambiaron la marca mientras corria). Codigo 3
        # para que el agente sepa que tiene que reintentar.
        sys.exit(3)
    shutil.rmtree(salida)
    shutil.rmtree(deriv, ignore_errors=True)


if __name__ == "__main__":
    main()
