#!/usr/bin/env python3
"""Fase 4: limpieza y auditoria de K: por huella de contenido.

Huella = (tamaño, blake2b de los primeros 64 KB + los ultimos 64 KB). No es un
hash completo: dos archivos distintos con el mismo tamaño y los mismos bordes
darian igual. Para fotos y videos de camara eso no pasa en la practica, pero
por eso nada se borra por huella sola si antes no coincide el tamaño exacto.

    python fase4_ejecucion_y_auditoria.py indice      # llena el cache de huellas
    python fase4_ejecucion_y_auditoria.py simulacro   # que haria, sin tocar nada
    python fase4_ejecucion_y_auditoria.py ejecutar --hacerlo
    python fase4_ejecucion_y_auditoria.py auditoria   # solo lectura (Tarea 2)

El cache vive en _fase4_huellas.db y se puede cortar y retomar: cada archivo se
vuelve a leer solo si cambio de tamaño o fecha.
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
import argparse
import hashlib
import os
import sqlite3
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

K = Path("K:/")
AQUI = _ROOT                      # los datos viven en la raiz
CACHE = _ROOT / "_salidas_locales" / "_fase4_huellas.db"
LOG = _ROOT / "_salidas_locales" / "txt_reportes" / "fase4_ejecucion.log"
DB_CURADOR = _ROOT / "photos_v2.db"
TROZO = 64 * 1024

# Nada de aca adentro se borra ni se mueve NUNCA, pase lo que pase.
INTOCABLES = [K / "Fotos" / "Galeria", K / "_facet_train",
              K / "Fotos" / "Backup" / "FotosLightroom", K / "_mascotas",
              K / "_subidas", K / "_previews", K / "_db", K / "_galeria",
              K / "_para_publicar", K / "_respaldos",
              K / "$RECYCLE.BIN", K / "System Volume Information"]

GALERIA = K / "Fotos" / "Galeria"
IA_V3 = K / "Fotos_Organizadas_IA_v3"
TEL_SIN = IA_V3 / "_Telefono (sin organizar)"
TEL_DEST = K / "Fotos" / "_Telefono"
RESCATADOS = TEL_DEST / "Rescatados_IA_v3"
SD = K / "SD_Jpeg_2026"
DCIM = K / "DCIM Jpeg 2026"
SECUNDARIAS = [K / "Lightroom_extraido", K / "Fotos" / "AOrdenar",
               K / "_duplicados_contenido", K / "_duplicados_cuarentena"]


def anotar(msg):
    linea = "%s  %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(linea, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(linea + "\n")


def protegida(p):
    """True si la ruta cae dentro de algo intocable."""
    p = Path(p).resolve()
    for raiz in INTOCABLES:
        try:
            p.relative_to(raiz.resolve())
            return True
        except ValueError:
            pass
    return False


# ------------------------------------------------------------------ huellas
def _cache():
    cx = sqlite3.connect(CACHE, timeout=60)
    cx.execute("""CREATE TABLE IF NOT EXISTS huella (
        ruta TEXT PRIMARY KEY, tam INTEGER, mtime INTEGER, h TEXT)""")
    cx.execute("CREATE INDEX IF NOT EXISTS ix_h ON huella(h)")
    cx.commit()
    return cx


def _huella_de(p, tam):
    """Tamaño + bordes. Un archivo chico se lee entero (es mas barato)."""
    b = hashlib.blake2b(digest_size=16)
    with open(p, "rb") as fh:
        if tam <= 2 * TROZO:
            b.update(fh.read())
        else:
            b.update(fh.read(TROZO))
            fh.seek(-TROZO, os.SEEK_END)
            b.update(fh.read(TROZO))
    return "%d:%s" % (tam, b.hexdigest())


def indexar(cx, raiz, etiqueta=None):
    """Huella de todo lo que cuelga de raiz, reusando el cache."""
    etiqueta = etiqueta or str(raiz)
    nuevos = vistos = 0
    t0 = time.time()
    pendientes = []
    for d, _subs, fs in os.walk(raiz):
        for f in fs:
            p = os.path.join(d, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            vistos += 1
            fila = cx.execute("SELECT tam, mtime FROM huella WHERE ruta = ?",
                              (p,)).fetchone()
            if fila and fila[0] == st.st_size and fila[1] == int(st.st_mtime):
                continue
            try:
                h = _huella_de(p, st.st_size)
            except OSError as e:
                anotar("  no pude leer %s: %s" % (p, e))
                continue
            pendientes.append((p, st.st_size, int(st.st_mtime), h))
            nuevos += 1
            if len(pendientes) >= 2000:
                cx.executemany("INSERT OR REPLACE INTO huella (ruta, tam, mtime, h) VALUES (?,?,?,?)", pendientes)
                cx.commit()
                pendientes.clear()
                print("\r  %s: %d/%d nuevos  %.0f arch/s" %
                      (etiqueta, nuevos, vistos, nuevos / max(1e-9, time.time() - t0)),
                      end="", flush=True)
    if pendientes:
        cx.executemany("INSERT OR REPLACE INTO huella (ruta, tam, mtime, h) VALUES (?,?,?,?)", pendientes)
        cx.commit()
    anotar("indice %s: %d archivos (%d leidos de nuevo) en %.0f s"
           % (etiqueta, vistos, nuevos, time.time() - t0))
    return vistos


def huellas_bajo(cx, raiz):
    """{huella} de lo ya indexado bajo esa carpeta."""
    pref = str(raiz).rstrip("\\/") + os.sep
    return {r[0] for r in cx.execute(
        "SELECT DISTINCT h FROM huella WHERE ruta LIKE ? ESCAPE '^'",
        (pref.replace("^", "^^").replace("%", "^%").replace("_", "^_") + "%",))}


def archivos_bajo(cx, raiz):
    """[(ruta, huella)] de lo ya indexado bajo esa carpeta."""
    pref = str(raiz).rstrip("\\/") + os.sep
    return cx.execute(
        "SELECT ruta, h FROM huella WHERE ruta LIKE ? ESCAPE '^'",
        (pref.replace("^", "^^").replace("%", "^%").replace("_", "^_") + "%",)).fetchall()


# --------------------------------------------------------- red de seguridad
def rutas_del_curador():
    """Las rutas que la base del curador considera vivas. Nada de eso se borra."""
    if not DB_CURADOR.exists():
        sys.exit("no encuentro photos_v2.db: sin esa red de seguridad no sigo")
    cx = sqlite3.connect("file:%s?mode=ro" % DB_CURADOR.as_posix(), uri=True)
    rutas = {r[0].replace("/", "\\").lower() for r in
             cx.execute("SELECT file_path FROM files WHERE file_path IS NOT NULL")}
    cx.close()
    return rutas


def borrables(rutas, en_curador):
    """Filtra lo intocable y lo que la base del curador tiene registrado."""
    ok, frenados = [], []
    for p in rutas:
        if protegida(p) or str(p).replace("/", "\\").lower() in en_curador:
            frenados.append(p)
        else:
            ok.append(p)
    return ok, frenados


# ------------------------------------------------------- hash completo
# La huella rapida (tamaño + bordes) solo sirve para BUSCAR candidatos: el
# 14/10/2026 se equivoco en 35 de 400 (fotos del mismo disparo reprocesadas,
# mismo tamaño exacto). Para borrar se exige el SHA-256 entero, que llena
# fase4_hash_completo.py. Sin sha, un archivo no se borra nunca.
def sha_de(cx, p):
    fila = cx.execute("SELECT sha FROM huella WHERE ruta = ?", (p,)).fetchone()
    return fila[0] if fila else None


def shas_bajo(cx, raiz):
    pref = str(raiz).rstrip("\\/") + os.sep
    like = pref.replace("^", "^^").replace("%", "^%").replace("_", "^_") + "%"
    return {r[0] for r in cx.execute(
        "SELECT DISTINCT sha FROM huella WHERE sha IS NOT NULL "
        "AND ruta LIKE ? ESCAPE '^'", (like,))}


# --------------------------------------------------------------- los 5 pasos
def paso1_sd(cx, hacer, en_curador):
    """Borrar el clon SD_Jpeg_2026, pero solo si DCIM lo tiene entero.

    Doble exigencia: misma ruta relativa Y mismo SHA-256 completo. Son 103 GB
    y no hay papelera, asi que la huella rapida no alcanza."""
    if not SD.exists():
        anotar("paso 1: SD_Jpeg_2026 ya no esta")
        return
    sd = {os.path.relpath(p, SD).lower(): (p, sha_de(cx, p)) for p, _h in archivos_bajo(cx, SD)}
    dc = {os.path.relpath(p, DCIM).lower(): (p, sha_de(cx, p)) for p, _h in archivos_bajo(cx, DCIM)}
    faltan = [r for r in sd if r not in dc]
    sin_sha = [r for r, v in sd.items() if not v[1]] + [r for r, v in dc.items() if not v[1]]
    distintos = [r for r in sd if r in dc and sd[r][1] != dc[r][1]]
    if sin_sha:
        anotar("paso 1 CANCELADO: %d archivos sin SHA-256 (corre fase4_hash_completo.py)"
               % len(sin_sha))
        return
    anotar("paso 1: SD %d archivos | DCIM %d | sin copia en DCIM: %d | con huella distinta: %d"
           % (len(sd), len(dc), len(faltan), len(distintos)))
    for r in (faltan + distintos)[:5]:
        anotar("   OJO %s" % r)
    if faltan or distintos:
        anotar("paso 1 CANCELADO: DCIM no cubre todo SD")
        return
    ok, frenados = borrables([v[0] for v in sd.values()], en_curador)
    anotar("paso 1: borrables %d | frenados por la base o por intocables %d"
           % (len(ok), len(frenados)))
    if frenados:
        anotar("paso 1 CANCELADO: hay archivos de SD registrados en photos_v2.db")
        return
    if not hacer:
        return
    n = 0
    for p in ok:
        os.unlink(p)
        n += 1
    for d, _s, _f in os.walk(SD, topdown=False):
        try:
            os.rmdir(d)
        except OSError:
            pass
    anotar("paso 1: borrados %d archivos de SD_Jpeg_2026" % n)


def paso2_telefono(cx, hacer, en_curador):
    """Lo del telefono sin organizar: lo repetido se borra, lo unico se muda."""
    if not TEL_SIN.exists():
        anotar("paso 2: la carpeta ya no esta")
        return
    conocidas = shas_bajo(cx, GALERIA) | shas_bajo(cx, TEL_DEST)
    archivos = archivos_bajo(cx, TEL_SIN)
    repes, unicos = [], []
    for p, h in archivos:
        s = sha_de(cx, p)
        (repes if s and s in conocidas else unicos).append((p, h))
    repes = [p for p, _h in repes]
    anotar("paso 2: %d archivos | iguales por SHA-256 a Galeria o _Telefono: %d | "
           "unicos a mudar: %d" % (len(archivos), len(repes), len(unicos)))
    ok, frenados = borrables(repes, en_curador)
    if frenados:
        anotar("paso 2: %d repetidos NO se borran (base del curador)" % len(frenados))
    if not hacer:
        return
    n = m = 0
    for p in ok:
        os.unlink(p)
        n += 1
    RESCATADOS.mkdir(parents=True, exist_ok=True)
    for p, h in unicos:
        dest = RESCATADOS / Path(p).name
        i = 0
        while dest.exists():
            i += 1
            dest = RESCATADOS / ("%s_%d%s" % (Path(p).stem, i, Path(p).suffix))
        os.replace(p, dest)                 # mismo volumen: es un rename
        cx.execute("UPDATE huella SET ruta = ? WHERE ruta = ?", (str(dest), p))
        m += 1
    cx.commit()
    for d, _s, _f in os.walk(TEL_SIN, topdown=False):
        try:
            os.rmdir(d)
        except OSError:
            pass
    anotar("paso 2: %d borrados, %d mudados a Rescatados_IA_v3" % (n, m))


def _purgar_contra_galeria(cx, raices, hacer, en_curador, etiqueta):
    """Borra lo que sea IGUAL por SHA-256 a algo de Galeria. Lo que la huella
    rapida marcaba y el sha desmiente queda (eran 2.895 archivos)."""
    gal = shas_bajo(cx, GALERIA)
    repes, unicos, falsos = [], 0, 0
    for raiz in raices:
        if not Path(raiz).exists():
            continue
        for p, h in archivos_bajo(cx, raiz):
            if TEL_SIN.exists() and str(p).lower().startswith(str(TEL_SIN).lower()):
                continue                    # eso lo maneja el paso 2
            if p.lower().endswith(".zip"):
                continue                    # los zip no se tocan
            s = sha_de(cx, p)
            if s and s in gal:
                repes.append(p)
            else:
                unicos += 1
                falsos += bool(s)           # tenia sha: la huella rapida mintio
    ok, frenados = borrables(repes, en_curador)
    anotar("%s: iguales por SHA-256 %d (borrables %d, frenados %d) | quedan %d "
           "(de esos, %d eran falsos positivos de la huella rapida)"
           % (etiqueta, len(repes), len(ok), len(frenados), unicos, falsos))
    if not hacer:
        return
    n = 0
    for p in ok:
        try:
            os.unlink(p)
            n += 1
        except OSError as e:
            anotar("  no pude borrar %s: %s" % (p, e))
    anotar("%s: borrados %d" % (etiqueta, n))


def paso5_vacias(hacer):
    raices = [IA_V3] + SECUNDARIAS
    vacias = []
    for raiz in raices:
        if not Path(raiz).exists():
            continue
        for d, _s, _f in os.walk(raiz, topdown=False):
            if Path(d) == Path(raiz) or protegida(d):
                continue
            try:
                if not os.listdir(d):
                    vacias.append(d)
                    if hacer:
                        os.rmdir(d)
            except OSError:
                pass
    anotar("paso 5: subcarpetas vacias %s: %d"
           % ("borradas" if hacer else "que hay", len(vacias)))


# ------------------------------------------------------------- Tarea 2
def auditoria(cx):
    print("\n=== REPORTE FASE 4 ===\n")
    gal = huellas_bajo(cx, GALERIA)
    otras = {"Lightroom_extraido": K / "Lightroom_extraido",
             "Fotos_Organizadas": K / "Fotos_Organizadas",
             "Fotos/AOrdenar": K / "Fotos" / "AOrdenar",
             "DCIM Jpeg 2026": DCIM,
             "Fotos/_Telefono": TEL_DEST}
    sets = {n: huellas_bajo(cx, r) for n, r in otras.items()}

    print("A. Sobrevivientes de Fotos_Organizadas_IA_v3")
    vivos = [(p, h) for p, h in archivos_bajo(cx, IA_V3) if os.path.exists(p)]
    print("   archivos vivos: %d" % len(vivos))
    print("   %-34s %d" % ("tambien en Galeria:", sum(1 for _p, h in vivos if h in gal)))
    for n, s in sets.items():
        print("   %-34s %d" % ("tambien en " + n + ":",
                               sum(1 for _p, h in vivos if h in s)))
    todos = gal.union(*sets.values())
    exclusivos = [p for p, h in vivos if h not in todos]
    print("   EXCLUSIVOS en todo el NAS: %d" % len(exclusivos))
    carp = {}
    for p in exclusivos:
        carp[os.path.dirname(p)] = carp.get(os.path.dirname(p), 0) + 1
    print("   Top 15 subcarpetas con exclusivos:")
    for d, n in sorted(carp.items(), key=lambda x: -x[1])[:15]:
        print("     %6d  %s" % (n, d))

    print("\nB. DCIM Jpeg 2026")
    dcim = archivos_bajo(cx, DCIM)
    print("   archivos: %d" % len(dcim))
    print("   ya estan en Galeria: %d" % sum(1 for _p, h in dcim if h in gal))
    ao = sets["Fotos/AOrdenar"] | sets["Lightroom_extraido"]
    print("   en AOrdenar o Lightroom_extraido: %d" % sum(1 for _p, h in dcim if h in ao))
    resto = gal | sets["Fotos_Organizadas"] | sets["Fotos/_Telefono"] | ao
    print("   unicos en el NAS: %d" % sum(1 for _p, h in dcim if h not in resto))

    print("\nC. Zips de Lightroom (solo encabezados) y _db")
    fuera = set()
    for raiz in (GALERIA, K / "Lightroom_extraido"):
        for p, h in archivos_bajo(cx, raiz):
            fuera.add((os.path.basename(p).lower(), int(h.split(":")[0])))
    for nombre, carpeta in (("FotosLightroom", K / "Fotos" / "Backup" / "FotosLightroom"),
                            ("Fotos_Organizadas/compresed", K / "Fotos_Organizadas" / "compresed")):
        zips = sorted(Path(carpeta).glob("*.zip")) if Path(carpeta).exists() else []
        print("   %s: %d zips" % (nombre, len(zips)))
        total = coinciden = 0
        detalle = []
        for z in zips:
            try:
                with zipfile.ZipFile(z) as zf:
                    items = [i for i in zf.infolist() if not i.is_dir()]
            except (zipfile.BadZipFile, OSError) as e:
                detalle.append((z.name, z.stat().st_size, "ILEGIBLE %s" % e, 0, 0))
                continue
            c = sum(1 for i in items
                    if (os.path.basename(i.filename).lower(), i.file_size) in fuera)
            total += len(items)
            coinciden += c
            detalle.append((z.name, z.stat().st_size, "", len(items), c))
        print("     fotos/videos adentro: %d | ya afuera por (nombre, tamano): %d"
              % (total, coinciden))
        if "compresed" in nombre:
            for n, tam, err, cuantos, c in detalle:
                print("     %-40s %7.2f GB  %5d adentro  %5d ya afuera %s"
                      % (n[:40], tam / 1e9, cuantos, c, err))
    print("\n   Archivos en K:\\_db:")
    if (K / "_db").exists():
        for p in sorted((K / "_db").glob("*")):
            st = p.stat()
            print("     %-46s %8.2f GB  %s" % (p.name[:46], st.st_size / 1e9,
                  datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("comando", choices=("indice", "simulacro", "ejecutar", "auditoria"))
    ap.add_argument("--hacerlo", action="store_true", help="sin esto no borra nada")
    a = ap.parse_args()
    cx = _cache()
    if a.comando == "indice":
        for raiz in ([GALERIA, TEL_DEST, IA_V3, SD, DCIM, K / "Fotos_Organizadas"]
                     + SECUNDARIAS):
            if Path(raiz).exists():
                indexar(cx, raiz)
        return
    en_curador = rutas_del_curador()
    anotar("red de seguridad: %d rutas registradas en photos_v2.db" % len(en_curador))
    if a.comando == "auditoria":
        auditoria(cx)
        return
    hacer = a.comando == "ejecutar" and a.hacerlo
    anotar("=== %s ===" % ("EJECUTANDO" if hacer else "SIMULACRO (no toca nada)"))
    paso1_sd(cx, hacer, en_curador)
    paso2_telefono(cx, hacer, en_curador)
    _purgar_contra_galeria(cx, [IA_V3], hacer, en_curador, "paso 3 (IA_v3)")
    _purgar_contra_galeria(cx, SECUNDARIAS, hacer, en_curador, "paso 4 (secundarias)")
    paso5_vacias(hacer)
    if not hacer:
        anotar("simulacro terminado: para hacerlo de verdad, 'ejecutar --hacerlo'")


if __name__ == "__main__":
    main()
