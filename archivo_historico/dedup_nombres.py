#!/usr/bin/env python3
"""Duplicados exactos del archivo, con prefiltro barato por nombre + tamaño.

Corre EN LA PI, que tiene el disco montado local: 22 ms por archivo contra
108 ms por Samba desde la notebook. No necesita el dock.

Por que no usar escanear_duplicados.py directo: ese hashea todo el archivo
(2,5 TB). Aca el indice de nombre+tamaño sale gratis del filesystem y deja
~188 mil candidatos, o sea 25 GB a leer en vez de 2500.

Por que nombre+tamaño y no solo nombre: la Sony reinicia el contador y le
pone DSC00660.ARW a fotos distintas de fechas distintas. Agrupar por nombre
solo y borrar seria perder fotos unicas.

    python3 dedup_nombres.py escanear     # solo lectura, escribe plan.csv
    python3 dedup_nombres.py test         # self-check
"""
import csv
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

RAIZ = Path(os.environ.get("DEDUP_RAIZ", "/mnt/fotos"))
SALIDA = Path(os.environ.get("DEDUP_SALIDA", Path.home() / "dedup"))
TROZO = 64 * 1024

FOTO = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".webp",
        ".arw", ".cr2", ".cr3", ".nef", ".dng", ".raf", ".orf"}

# Carpetas que no entran al indice. La cuarentena queda afuera a proposito:
# lo que ya se resolvio no debe volver a emparejarse con un archivo vivo.
SALTEAR = {"_duplicados_cuarentena", "_galeria", "_thumbs_curador",
           "_vistas_curador", "_autocull_curador", "$RECYCLE.BIN",
           "System Volume Information", "_no_procesadas"}

# Portadas de 5_duplicados.py — misma regla, para no tener dos criterios
# distintos de "cual copia es la buena" en el mismo proyecto.
RUTAS_SOSPECHOSAS = ("whatsapp", "download", "descarga", "telegram", "cache",
                     "temp", "papelera", "recycle", "copia", "copy", " (1)",
                     "screenshot", "captura", "thumbnail", "miniatura",
                     "_duplicados_contenido", "carpetasexternas", "_cuarentena")
RUTAS_BUENAS = ("fotos_organizadas",)


def penalizacion_ruta(path):
    low = str(path).lower()
    return (sum(1 for s in RUTAS_SOSPECHOSAS if s in low)
            - sum(1 for s in RUTAS_BUENAS if s in low))


def indexar(raiz):
    """(nombre, tamaño) -> [rutas]. Solo metadata, no abre ningun archivo."""
    grupos = defaultdict(list)
    total = 0
    pila = [raiz]
    while pila:
        aqui = pila.pop()
        try:
            with os.scandir(aqui) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if e.name not in SALTEAR and not e.name.startswith("."):
                                pila.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            ext = os.path.splitext(e.name)[1].lower()
                            if ext in FOTO:
                                total += 1
                                grupos[(e.name.lower(), e.stat().st_size)].append(e.path)
                    except OSError:
                        continue
        except OSError:
            continue
    return grupos, total


def firma(ruta, tam):
    """Hash de tres pedazos: principio, medio y final.

    Con nombre y tamaño ya iguales, esto separa las colisiones de contador
    (fotos distintas con el mismo nombre) sin leer el archivo entero. No es
    prueba criptografica de identidad — por eso el paso final mueve a
    cuarentena en vez de borrar.
    """
    h = hashlib.blake2b(digest_size=16)
    h.update(str(tam).encode())
    with open(ruta, "rb") as fh:
        if tam <= 3 * TROZO:
            h.update(fh.read())
        else:
            for pos in (0, tam // 2, tam - TROZO):
                fh.seek(pos)
                h.update(fh.read(TROZO))
    return h.hexdigest()


def elegir(rutas):
    """Entre copias identicas solo importa donde vive cada una.

    Nada de 'la ruta mas corta': eso ya fallo antes y favorecia justo al arbol
    con mas daño. Menor penalizacion primero, despues la mas vieja (el original),
    y el nombre para que dos corridas den lo mismo.
    """
    def orden(r):
        try:
            mt = os.stat(r).st_mtime
        except OSError:
            mt = float("inf")
        return (penalizacion_ruta(r), mt, r)
    return sorted(rutas, key=orden)


def razon(keeper, descarte):
    pk, pd = penalizacion_ruta(keeper), penalizacion_ruta(descarte)
    if pd > pk:
        return "esta en carpeta de copias" if pd > 0 else "el keeper esta en el arbol organizado"
    return "copia identica, se conserva la mas antigua"


def escanear():
    SALIDA.mkdir(parents=True, exist_ok=True)
    cache_p = SALIDA / "firmas.json"
    plan_p = SALIDA / "plan.csv"

    print(f"Fase 1: indexando {RAIZ} (solo metadata)...", flush=True)
    t0 = time.perf_counter()
    grupos, total = indexar(RAIZ)
    cand = {k: v for k, v in grupos.items() if len(v) > 1}
    archivos = sum(len(v) for v in cand.values())
    print(f"  {total:,} fotos, {len(cand):,} grupos candidatos, "
          f"{archivos:,} archivos a verificar  ({time.perf_counter()-t0:.0f}s)\n", flush=True)

    firmas = {}
    if cache_p.exists():
        firmas = json.loads(cache_p.read_text())
        print(f"Fase 2: retomando, {len(firmas):,} firmas ya calculadas", flush=True)
    else:
        print("Fase 2: leyendo 192 KB de cada candidato...", flush=True)

    t0, hechos, ilegibles = time.perf_counter(), 0, 0
    for (nombre, tam), rutas in cand.items():
        for r in rutas:
            if r in firmas:
                continue
            try:
                firmas[r] = firma(r, tam)
            except OSError:
                firmas[r] = None
                ilegibles += 1
            hechos += 1
            if hechos % 2000 == 0:
                vel = hechos / (time.perf_counter() - t0)
                faltan = (archivos - len(firmas)) / vel if vel else 0
                print(f"  {len(firmas):,}/{archivos:,}  {vel:.0f}/s  "
                      f"faltan ~{faltan/60:.0f} min", flush=True)
                cache_p.write_text(json.dumps(firmas))
    cache_p.write_text(json.dumps(firmas))
    print(f"  listo en {(time.perf_counter()-t0)/60:.0f} min"
          + (f", {ilegibles} ilegibles" if ilegibles else "") + "\n", flush=True)

    print("Fase 3: agrupando por contenido y eligiendo keeper...", flush=True)
    confirmados, colisiones, libera = 0, 0, 0
    with open(plan_p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["grupo", "accion", "ruta", "bytes", "penalizacion", "razon"])
        gid = 0
        for (nombre, tam), rutas in cand.items():
            porfirma = defaultdict(list)
            for r in rutas:
                if firmas.get(r):
                    porfirma[firmas[r]].append(r)
            if len(porfirma) > 1:
                colisiones += 1
            for iguales in porfirma.values():
                if len(iguales) < 2:
                    continue
                gid += 1
                confirmados += 1
                keeper, *resto = elegir(iguales)
                w.writerow([gid, "CONSERVAR", keeper, tam, penalizacion_ruta(keeper), ""])
                for d in resto:
                    libera += tam
                    w.writerow([gid, "duplicado", d, tam, penalizacion_ruta(d),
                                razon(keeper, d)])

    print(f"\n  grupos confirmados     : {confirmados:,}")
    print(f"  grupos con colision de nombre (contador reusado): {colisiones:,}")
    print(f"  espacio recuperable    : {libera/1e9:,.1f} GB")
    print(f"\n  plan -> {plan_p}")
    print("  NO se movio ni borro nada.")


def test():
    import tempfile
    global RAIZ, SALIDA
    tmp = Path(tempfile.mkdtemp())
    RAIZ, SALIDA = tmp / "fotos", tmp / "out"
    (RAIZ / "Fotos_Organizadas" / "a").mkdir(parents=True)
    (RAIZ / "Descargas").mkdir(parents=True)
    (RAIZ / "_duplicados_cuarentena").mkdir(parents=True)

    # dos copias identicas del mismo archivo, en carpetas de distinta calidad
    cuerpo = bytes(range(256)) * 1200          # 300 KB, mas que 3 trozos
    for p in (RAIZ / "Fotos_Organizadas" / "a" / "DSC1.ARW", RAIZ / "Descargas" / "DSC1.ARW"):
        p.write_bytes(cuerpo)

    # colision de contador: mismo nombre y MISMO tamaño, contenido distinto
    otro = bytearray(cuerpo)
    otro[5] ^= 0xFF
    otro[len(otro) // 2] ^= 0xFF
    (RAIZ / "Fotos_Organizadas" / "DSC2.ARW").write_bytes(cuerpo)
    (RAIZ / "Descargas" / "DSC2.ARW").write_bytes(bytes(otro))

    # la cuarentena no entra al indice
    (RAIZ / "_duplicados_cuarentena" / "DSC1.ARW").write_bytes(cuerpo)

    grupos, total = indexar(RAIZ)
    assert total == 4, f"la cuarentena entro al indice: {total}"
    cand = {k: v for k, v in grupos.items() if len(v) > 1}
    assert len(cand) == 2, cand

    f = {r: firma(r, len(cuerpo)) for v in cand.values() for r in v}
    d1 = [r for r in f if r.endswith("DSC1.ARW")]
    d2 = [r for r in f if r.endswith("DSC2.ARW")]
    assert f[d1[0]] == f[d1[1]], "copias identicas dieron firmas distintas"
    assert f[d2[0]] != f[d2[1]], "COLISION NO DETECTADA: borraria una foto unica"

    keeper, *resto = elegir(d1)
    assert "Fotos_Organizadas" in keeper, f"eligio la copia de Descargas: {keeper}"
    assert penalizacion_ruta(resto[0]) > penalizacion_ruta(keeper)

    # un archivo mas chico que los tres trozos se lee entero, no explota
    chico = RAIZ / "chico.jpg"
    chico.write_bytes(b"hola")
    assert firma(chico, 4) == firma(chico, 4)

    print("ok")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "escanear"
    if cmd == "test":
        test()
    elif cmd == "escanear":
        escanear()
    else:
        sys.exit(__doc__)
