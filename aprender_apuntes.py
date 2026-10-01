r"""Claude aprende de lo que eligio Renato y lo deja escrito en data/apuntes_renato.md.

    $AC = "C:\Renzo\Proyectos\Autocull\venv\Scripts\python.exe"     # (lee RAW: necesita el venv)
    & $AC aprender_apuntes.py --par <carrete> <seleccion> [--par ...]

Mismos pares que `python -m autocull aprender` (carpeta de elegidas o CSV de la galeria). Por cada
fecha arma 2 hojas de contacto con elegidas y 2 con NO elegidas al azar (9 por hoja), y Claude Sonnet
compara y reescribe los apuntes: que busca Renato, que descarta, cuantas fechas respaldan cada patron.
Los apuntes son acumulativos: cada fecha confirma, corrige o agrega a lo anterior.

Es el complemento del ranker numerico: el ranker aprende pesos sobre lo que Autocull mide; los
apuntes capturan lo que Autocull no mide (el momento, la emocion, a quien se fotografia) y los lee
el juez final de seleccion_ig.py. Las fechas hechas quedan en data/selecciones_renato/apuntes_hechos.json:
volver a correrlo salta lo hecho. Para rehacer desde cero, borra ese JSON y apuntes_renato.md.
"""
import argparse
import hashlib
import json
import os
import random
import sys
from pathlib import Path

import seleccion_ig as s
from autocull.aprender import archivos_carrete, clave, leer_seleccion

AQUI = Path(__file__).resolve().parent
HECHOS = AQUI / "data" / "selecciones_renato" / "apuntes_hechos.json"
TRABAJO = AQUI / "_autocull_aprender" / "hojas_apuntes"
MUESTRA = 18  # por lado: 2 hojas de 9
RAW = (".arw", ".cr2", ".cr3", ".nef", ".dng", ".raf", ".orf", ".rw2")


def legible(ruta, carpeta, i):
    """JPEG que PIL pueda abrir: los RAW de 2025 pasan por su preview embebido (como Autocull)."""
    if not ruta.lower().endswith(RAW):
        return Path(ruta)
    import cv2
    from autocull.extractors.preview import load_image
    destino = carpeta / f"raw_{i:03d}.jpg"
    if not destino.exists():
        img = load_image(ruta)
        h, w = img.shape[:2]
        f = 1200 / max(h, w)
        cv2.imwrite(str(destino), cv2.resize(img, (int(w * f), int(h * f))) if f < 1 else img)
    return destino


def fecha(carrete, seleccion, previos):
    nombre = " - ".join(Path(carrete).parts[-2:])
    elegidas_sel = leer_seleccion(seleccion)
    todas = archivos_carrete(carrete, seleccion)
    si = [p for p in todas if clave(p) in elegidas_sel]
    no = [p for p in todas if clave(p) not in elegidas_sel]
    if not si:
        raise RuntimeError("ninguna elegida calzo por nombre")
    azar = random.Random(nombre)  # misma muestra si se repite la corrida
    si, no = azar.sample(si, min(MUESTRA, len(si))), azar.sample(no, min(MUESTRA, len(no)))
    carpeta = TRABAJO / hashlib.sha1(nombre.encode()).hexdigest()[:10]
    carpeta.mkdir(parents=True, exist_ok=True)
    hojas = {"elegidas": [], "no_elegidas": []}
    for lado, fotos in (("elegidas", si), ("no_elegidas", no)):
        for k in range(0, len(fotos), 9):
            grupo = fotos[k:k + 9]
            archivo = f"{lado}_{k // 9 + 1}.jpg"
            rutas = [legible(p, carpeta, k + j + (0 if lado == "elegidas" else 500)) for j, p in enumerate(grupo)]
            s.hoja(rutas, carpeta / archivo, [f"{lado[0].upper()}{k + j + 1}" for j in range(len(grupo))],
                   titulo=f"{'ELEGIDAS por Renato' if lado == 'elegidas' else 'NO elegidas (al azar)'} - {nombre}")
            hojas[lado].append(archivo)
    prompt = f"""Estas aprendiendo el gusto de Renato, cliente de la Liga ExScuola (futbol 7 amateur, casi
siempre de noche). De cada fecha, Renzo (el fotografo) le muestra el carrete y Renato elige las fotos
de su entrega; el resto queda fuera.

Fecha: {nombre}. Renato eligio {len(elegidas_sel)} fotos de un carrete de {len(todas)}.
Lee con Read estas hojas de contacto:
- ELEGIDAS por Renato: {', '.join(hojas['elegidas'])}
- NO elegidas, muestra al azar: {', '.join(hojas['no_elegidas'])}

Apuntes actuales, de fechas anteriores (vacio si es la primera):
<apuntes>
{previos or '(todavia no hay)'}
</apuntes>

Reescribe los apuntes completos incorporando esta fecha. Que tienen las elegidas que no tienen las
otras: momento (accion, celebracion, arenga, podio), encuadre y cercania, a quien se fotografia
(jugadores, arqueros, publico, staff), grupales vs individuales, tecnica (nitidez, luz). Que descarta.
Confirma, corrige o elimina lo anterior segun lo que veas aca, y anota entre parentesis cuantas
fechas respaldan cada patron, p. ej. "(4 fechas)". Separa lo firme de lo tentativo. Concreto y breve:
maximo ~60 lineas de markdown, en español de Chile (tuteo, nunca voseo).

Responde con este formato exacto (texto plano, NO JSON):
HALLAZGO: <1 o 2 lineas: lo nuevo o lo corregido con esta fecha>
{SEPARADOR}
<los apuntes completos actualizados, en markdown>"""
    texto, uso = s.claude_texto(prompt, carpeta, s.MODELO_CLAUDE)
    hallazgo, apuntes = partir(texto)
    return nombre, apuntes, hallazgo, uso


# Texto plano y no JSON: markdown de 60 lineas dentro de un string JSON trae saltos de linea y
# comillas sin escapar, y 3 de 10 fechas fallaron asi la primera noche (1/10/2026).
SEPARADOR = "===APUNTES==="


def partir(texto):
    """'HALLAZGO: ...\\n===APUNTES===\\n<md>' -> (hallazgo, md). Sin separador no hay apuntes: error."""
    if SEPARADOR not in texto:
        raise ValueError(f"respuesta sin {SEPARADOR}: {texto[:200]}")
    cabeza, apuntes = texto.split(SEPARADOR, 1)
    hallazgo = cabeza.strip().removeprefix("HALLAZGO:").strip()
    if len(apuntes.strip()) < 50:
        raise ValueError("apuntes vacios")
    return hallazgo, apuntes.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--par", nargs=2, action="append", metavar=("CARRETE", "SELECCION"), default=[])
    a = ap.parse_args()
    hechos = json.loads(HECHOS.read_text(encoding="utf-8")) if HECHOS.exists() else {}
    for carrete, seleccion in a.par:
        if carrete in hechos:
            print(f"ya hecho: {carrete}")
            continue
        previos = s.APUNTES.read_text(encoding="utf-8") if s.APUNTES.exists() else ""
        try:
            nombre, apuntes, hallazgo, uso = fecha(carrete, seleccion, previos)
        except Exception as e:  # una fecha mala no tumba las demas
            print(f"SALTO {carrete}: {str(e)[:300]}", file=sys.stderr, flush=True)
            continue
        s.APUNTES.write_text(apuntes + "\n", encoding="utf-8")
        hechos[carrete] = {"fecha": nombre, "hallazgo": hallazgo, "uso": uso}
        HECHOS.write_text(json.dumps(hechos, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{nombre}: {hallazgo}", flush=True)
    print(f"\napuntes en {s.APUNTES}")


if __name__ == "__main__":
    main()
