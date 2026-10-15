#!/usr/bin/env python3
"""Fase 5, pasos 1 a 3: desmantelar IA_v3, juntar cuarentenas y ordenar la raiz.

Todo movimiento es dentro del mismo volumen (un rename, instantaneo) y cada vez
que una ruta cambia se actualiza photos_v2.db en la misma pasada. Nada se borra
si su gemelo no esta confirmado por SHA-256 completo: la huella rapida se
equivoco en 35 de 400 (14/10/2026).

    python fase5_mover.py simulacro
    python fase5_mover.py ejecutar --hacerlo
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
import os
import shutil
import sqlite3
import sys
from pathlib import Path

from fase4_auditoria import Shas, igual_en
from fase4_ejecucion_y_auditoria import (DCIM, GALERIA, IA_V3, K, TEL_DEST,
                                         _cache, anotar, archivos_bajo)

DB = os.path.join(str(_ROOT), "photos_v2.db")
RESCATE_IA = K / "Fotos" / "AOrdenar" / "Rescatados_IA_v3"
CUARENTENA = K / "_Cuarentena"
DUP_UNICOS = CUARENTENA / "duplicados_unicos_revisar"
RAW_ILEGIBLES_VIEJO = K / "_cuarentena_raw_ilegibles"
RAW_ILEGIBLES = CUARENTENA / "raw_ilegibles"
DUPS = [K / "_duplicados_contenido", K / "_duplicados_cuarentena"]
ORGANIZADAS = K / "Fotos_Organizadas"
LIGHTROOM = K / "Lightroom_extraido"
CONTRA = [GALERIA, LIGHTROOM, K / "Fotos" / "AOrdenar", TEL_DEST, ORGANIZADAS]


class Base:
    """photos_v2.db: mover una ruta, o marcar la fila como reemplazada."""

    def __init__(self, hacer):
        self.hacer = hacer
        self.cx = sqlite3.connect(DB, timeout=120)
        self.ocupadas = {p for (p,) in self.cx.execute("SELECT file_path FROM files")}
        self.movidas = self.marcadas = 0

    def mover(self, viejo, nuevo):
        if viejo not in self.ocupadas:
            return
        if nuevo in self.ocupadas:               # ya hay una fila con esa ruta
            if self.hacer:
                self.cx.execute("UPDATE files SET borrado = 1 WHERE file_path = ?", (viejo,))
            self.marcadas += 1
            return
        if self.hacer:
            self.cx.execute("UPDATE files SET file_path = ? WHERE file_path = ?",
                            (nuevo, viejo))
        self.ocupadas.discard(viejo)
        self.ocupadas.add(nuevo)
        self.movidas += 1

    def cerrar(self):
        if self.hacer:
            self.cx.commit()
        anotar("  base: %d filas reapuntadas, %d marcadas borrado=1" % (self.movidas, self.marcadas))
        self.cx.close()


def _mover(p, destino, hacer, base):
    destino.parent.mkdir(parents=True, exist_ok=True) if hacer else None
    final = destino
    i = 0
    while hacer and final.exists():
        i += 1
        final = destino.with_name("%s_%d%s" % (destino.stem, i, destino.suffix))
    if hacer:
        os.replace(p, final)
    base.mover(p, str(final))
    return final


def paso1_ia_v3(cx, shas, hacer, base):
    """Los exclusivos se mudan a AOrdenar\\Rescatados_IA_v3; los que tienen
    gemelo confirmado por sha se borran. IA_v3 desaparece de la raiz."""
    if not IA_V3.exists():
        anotar("paso 1: IA_v3 ya no esta")
        return
    idx = {}
    for raiz in CONTRA:
        if raiz == IA_V3 or not Path(raiz).exists():
            continue
        for p, h in archivos_bajo(cx, raiz):
            idx.setdefault(h, []).append(p)
    vivos = [(p, h) for p, h in archivos_bajo(cx, IA_V3) if os.path.exists(p)]
    repes = [p for p, h in vivos if h in idx and igual_en(shas, p, idx[h])]
    exclusivos = [p for p, _h in vivos if p not in set(repes)]
    anotar("paso 1: %d vivos | con gemelo confirmado por sha %d | exclusivos a mudar %d"
           % (len(vivos), len(repes), len(exclusivos)))
    if not hacer:
        return
    for p in exclusivos:
        rel = Path(p).relative_to(IA_V3)
        _mover(p, RESCATE_IA / rel, hacer, base)
    huella_de = dict(vivos)
    n = 0
    for p in repes:
        gemelo = next((q for q in idx.get(huella_de[p], []) if os.path.exists(q)), None)
        if gemelo:                               # el gemelo tiene que seguir ahi
            base.mover(p, gemelo)
            os.unlink(p)
            n += 1
    anotar("paso 1: %d mudados, %d borrados" % (len(exclusivos), n))
    for d, _s, _f in os.walk(IA_V3, topdown=False):
        try:
            os.rmdir(d)
        except OSError:
            pass
    anotar("paso 1: IA_v3 %s" % ("eliminada" if not IA_V3.exists() else "TODAVIA tiene cosas"))


def paso2_cuarentenas(cx, shas, hacer, base):
    idx = {}
    for raiz in CONTRA:
        if not Path(raiz).exists():
            continue
        for p, h in archivos_bajo(cx, raiz):
            idx.setdefault(h, []).append(p)
    repes, unicos = [], []
    for raiz in DUPS:
        if not Path(raiz).exists():
            continue
        for p, h in archivos_bajo(cx, raiz):
            if not os.path.exists(p):
                continue
            (repes if h in idx and igual_en(shas, p, idx[h]) else unicos).append(p)
    anotar("paso 2: cuarentenas | repetidos confirmados %d | unicos a juntar %d"
           % (len(repes), len(unicos)))
    # esta carpeta nunca se indexo (no hacia falta para la Fase 4): se lee del
    # disco y se mueve entera, que es un rename, actualizando sus rutas
    raws = []
    if RAW_ILEGIBLES_VIEJO.exists():
        for d, _s, fs in os.walk(RAW_ILEGIBLES_VIEJO):
            raws += [os.path.join(d, f) for f in fs]
    anotar("paso 2: raw ilegibles a mover: %d" % len(raws))
    if not hacer:
        return
    for p in repes:
        gemelo = next((q for q in idx.get(
            cx.execute("SELECT h FROM huella WHERE ruta = ?", (p,)).fetchone()[0], [])
            if os.path.exists(q)), None)
        if gemelo:
            base.mover(p, gemelo)
            os.unlink(p)
    for p in unicos:
        _mover(p, DUP_UNICOS / Path(p).name, hacer, base)
    for p in raws:
        _mover(p, RAW_ILEGIBLES / Path(p).name, hacer, base)
    for raiz in DUPS + [RAW_ILEGIBLES_VIEJO]:
        if not Path(raiz).exists():
            continue
        for d, _s, _f in os.walk(raiz, topdown=False):
            try:
                os.rmdir(d)
            except OSError:
                pass
        anotar("paso 2: %s %s" % (Path(raiz).name,
                                 "eliminada" if not Path(raiz).exists() else "TODAVIA tiene cosas"))


def paso3_raiz(cx, hacer, base):
    """Mover carpetas enteras de la raiz adentro de Fotos/."""
    planes = [(DCIM, K / "Fotos" / "DCIM_Jpeg_2026"),
              (LIGHTROOM, K / "Fotos" / "Lightroom_extraido")]
    for origen, destino in planes:
        if not Path(origen).exists():
            continue
        archivos = [p for p, _h in archivos_bajo(cx, origen)]
        anotar("paso 3: %s -> %s (%d archivos)" % (Path(origen).name, destino, len(archivos)))
        if not hacer:
            continue
        destino.parent.mkdir(parents=True, exist_ok=True)
        os.replace(origen, destino)              # un rename de carpeta entera
        for p in archivos:
            base.mover(p, str(destino / Path(p).relative_to(origen)))
        cx.executemany("UPDATE huella SET ruta = ? WHERE ruta = ?",
                       [(str(destino / Path(p).relative_to(origen)), p) for p in archivos])
        cx.commit()
    # Fotos_Organizadas: los zips a Backup, las fotos a AOrdenar
    if ORGANIZADAS.exists():
        zips = sorted((ORGANIZADAS / "compresed").glob("*.zip")) if (ORGANIZADAS / "compresed").exists() else []
        fotos = [p for p, _h in archivos_bajo(cx, ORGANIZADAS) if not p.lower().endswith(".zip")]
        anotar("paso 3: Fotos_Organizadas | %d zips a Backup | %d fotos a AOrdenar"
               % (len(zips), len(fotos)))
        if hacer:
            dz = K / "Fotos" / "Backup" / "Zips_Ligas_y_Semestre"
            dz.mkdir(parents=True, exist_ok=True)
            for z in zips:
                _mover(str(z), dz / z.name, hacer, base)
            for p in fotos:
                nuevo = _mover(p, K / "Fotos" / "AOrdenar" / "Fotos_Organizadas"
                               / Path(p).relative_to(ORGANIZADAS), hacer, base)
                cx.execute("UPDATE huella SET ruta = ? WHERE ruta = ?", (str(nuevo), p))
            cx.commit()
            for d, _s, _f in os.walk(ORGANIZADAS, topdown=False):
                try:
                    os.rmdir(d)
                except OSError:
                    pass
            anotar("paso 3: Fotos_Organizadas %s"
                   % ("eliminada" if not ORGANIZADAS.exists() else "TODAVIA tiene cosas"))
    # _db: solo los archivos realmente vacios
    if (K / "_db").exists():
        vacios = [p for p in (K / "_db").glob("*") if p.is_file() and p.stat().st_size == 0]
        chicos = [(p.name, p.stat().st_size) for p in (K / "_db").glob("*")
                  if p.is_file() and 0 < p.stat().st_size < 50 * 1024 * 1024]
        anotar("paso 3: _db | de 0 bytes: %d | chicos (<50 MB, NO se tocan): %s"
               % (len(vacios), chicos))
        if hacer:
            for p in vacios:
                p.unlink()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "simulacro"
    hacer = cmd == "ejecutar" and "--hacerlo" in sys.argv
    cx = _cache()
    shas = Shas(cx)
    base = Base(hacer)
    anotar("=== FASE 5 pasos 1-3: %s ===" % ("EJECUTANDO" if hacer else "SIMULACRO"))
    paso1_ia_v3(cx, shas, hacer, base)
    paso2_cuarentenas(cx, shas, hacer, base)
    paso3_raiz(cx, hacer, base)
    shas.cerrar()
    base.cerrar()
    if not hacer:
        anotar("simulacro terminado")


if __name__ == "__main__":
    main()
