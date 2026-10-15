#!/usr/bin/env python3
"""Tarea 2: auditoria de sobrevivientes (solo lectura).

Los cruces se deciden por SHA-256 completo, no por la huella rapida: esa se
equivoco en 35 de 400 (8,75 %) el 14/10/2026. La huella rapida se usa solo
para encontrar los pares candidatos; de cada par se calcula el sha (y se
guarda en el cache, asi la proxima corrida es instantanea).

    python fase4_auditoria.py
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
import hashlib
import os
import time
from datetime import datetime
from pathlib import Path
import zipfile

from fase4_ejecucion_y_auditoria import (DCIM, GALERIA, IA_V3, K, TEL_DEST,
                                         _cache, archivos_bajo)

OTRAS = {"Lightroom_extraido": K / "Lightroom_extraido",
         "Fotos_Organizadas": K / "Fotos_Organizadas",
         "Fotos/AOrdenar": K / "Fotos" / "AOrdenar",
         "DCIM Jpeg 2026": DCIM,
         "Fotos/_Telefono": TEL_DEST}


def limpiar_cache(cx):
    """Saca del cache lo que ya no existe (lo que borro la ejecucion)."""
    fuera = [(p,) for (p,) in cx.execute("SELECT ruta FROM huella")
             if not os.path.exists(p)]
    cx.executemany("DELETE FROM huella WHERE ruta = ?", fuera)
    cx.commit()
    print("cache: %d entradas de archivos que ya no estan, borradas" % len(fuera))


def sha_fichero(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(4 << 20), b""):
            h.update(b)
    return h.hexdigest()


class Shas:
    """SHA-256 bajo demanda, cacheado. Solo se calcula el de los pares que la
    huella rapida marco como candidatos."""

    def __init__(self, cx):
        self.cx = cx
        self.memo = {p: s for p, s in cx.execute(
            "SELECT ruta, sha FROM huella WHERE sha IS NOT NULL")}
        self.nuevos = 0

    def de(self, p):
        if p in self.memo:
            return self.memo[p]
        try:
            s = sha_fichero(p)
        except OSError:
            s = None
        self.memo[p] = s
        if s:
            self.cx.execute("UPDATE huella SET sha = ? WHERE ruta = ?", (s, p))
            self.nuevos += 1
            if self.nuevos % 500 == 0:
                self.cx.commit()
                print("\r  sha nuevos: %d" % self.nuevos, end="", flush=True)
        return s

    def cerrar(self):
        self.cx.commit()


def igual_en(shas, p, candidatos):
    """True si p es identico (sha) a alguno de los candidatos por huella."""
    s = shas.de(p)
    return bool(s) and any(shas.de(q) == s for q in candidatos)


def main():
    cx = _cache()
    limpiar_cache(cx)
    shas = Shas(cx)
    t0 = time.time()

    # indice por huella rapida de cada carpeta: huella -> [rutas]
    def indice(raiz):
        d = {}
        for p, h in archivos_bajo(cx, raiz):
            d.setdefault(h, []).append(p)
        return d

    gal = indice(GALERIA)
    otras = {n: indice(r) for n, r in OTRAS.items()}
    print("=== REPORTE FASE 4 ===\n")

    print("A. Sobrevivientes de Fotos_Organizadas_IA_v3")
    vivos = [(p, h) for p, h in archivos_bajo(cx, IA_V3) if os.path.exists(p)]
    print("   archivos vivos: %d" % len(vivos))
    en_gal = [p for p, h in vivos if h in gal and igual_en(shas, p, gal[h])]
    print("   %-32s %d" % ("identicos a algo de Galeria:", len(en_gal)))
    en_otras = {}
    for n, idx in otras.items():
        en_otras[n] = {p for p, h in vivos if h in idx and igual_en(shas, p, idx[h])}
        print("   %-32s %d" % ("identicos a algo de " + n + ":", len(en_otras[n])))
    repetidos = set(en_gal).union(*en_otras.values())
    exclusivos = [p for p, _h in vivos if p not in repetidos]
    print("   EXCLUSIVOS en todo el NAS: %d" % len(exclusivos))
    carp = {}
    for p in exclusivos:
        carp[os.path.dirname(p)] = carp.get(os.path.dirname(p), 0) + 1
    print("   Top 15 subcarpetas con exclusivos:")
    for d, n in sorted(carp.items(), key=lambda x: -x[1])[:15]:
        print("     %6d  %s" % (n, d))

    print("\nB. DCIM Jpeg 2026")
    dcim = [(p, h) for p, h in archivos_bajo(cx, DCIM) if os.path.exists(p)]
    print("   archivos: %d" % len(dcim))
    d_gal = [p for p, h in dcim if h in gal and igual_en(shas, p, gal[h])]
    print("   identicos a algo de Galeria: %d" % len(d_gal))
    ao = {}
    for n in ("Fotos/AOrdenar", "Lightroom_extraido"):
        for h, ps in otras[n].items():
            ao.setdefault(h, []).extend(ps)
    d_ao = [p for p, h in dcim if h in ao and igual_en(shas, p, ao[h])]
    print("   identicos a algo de AOrdenar o Lightroom_extraido: %d" % len(d_ao))
    resto = dict(gal)
    for n in ("Fotos_Organizadas", "Fotos/_Telefono"):
        for h, ps in otras[n].items():
            resto.setdefault(h, []).extend(ps)
    for h, ps in ao.items():
        resto.setdefault(h, []).extend(ps)
    unicos = [p for p, h in dcim if not (h in resto and igual_en(shas, p, resto[h]))]
    print("   unicos en el NAS: %d" % len(unicos))

    print("\nC. Zips de Lightroom (solo encabezados) y K:\\_db")
    fuera = set()
    for raiz in (GALERIA, K / "Lightroom_extraido"):
        for p, h in archivos_bajo(cx, raiz):
            fuera.add((os.path.basename(p).lower(), int(h.split(":")[0])))
    for nombre, carpeta in (("Fotos/Backup/FotosLightroom", K / "Fotos" / "Backup" / "FotosLightroom"),
                            ("Fotos_Organizadas/compresed", K / "Fotos_Organizadas" / "compresed")):
        zips = sorted(Path(carpeta).glob("*.zip")) if Path(carpeta).exists() else []
        total = coinciden = 0
        detalle = []
        for z in zips:
            try:
                with zipfile.ZipFile(z) as zf:
                    items = [i for i in zf.infolist() if not i.is_dir()]
            except (zipfile.BadZipFile, OSError) as e:
                detalle.append((z.name, z.stat().st_size, 0, 0, "ILEGIBLE %s" % e))
                continue
            c = sum(1 for i in items
                    if (os.path.basename(i.filename).lower(), i.file_size) in fuera)
            total += len(items)
            coinciden += c
            detalle.append((z.name, z.stat().st_size, len(items), c, ""))
        print("   %s: %d zips | %d fotos/videos adentro | %d ya estan afuera "
              "(por nombre y tamano exacto)" % (nombre, len(zips), total, coinciden))
        if "compresed" in nombre:
            for n, tam, cuantos, c, err in detalle:
                print("     %-38s %6.2f GB %6d adentro %6d afuera %s"
                      % (n[:38], tam / 1e9, cuantos, c, err))
    print("\n   K:\\_db:")
    if (K / "_db").exists():
        for p in sorted((K / "_db").glob("*")):
            st = p.stat()
            print("     %-44s %7.2f GB  %s" % (p.name[:44], st.st_size / 1e9,
                  datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")))
    shas.cerrar()
    print("\n(sha calculados en esta corrida: %d | %.0f min)"
          % (shas.nuevos, (time.time() - t0) / 60))


if __name__ == "__main__":
    main()
