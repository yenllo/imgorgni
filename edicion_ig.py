r"""Edicion completa automatica para Instagram, en Lightroom (14/10/2026).

    python edicion_ig.py <carpeta con los RAW> <id del trabajo> [--version N] [--abrir]

Arma una RECETA por foto y la deja en la cola del plugin (Cola.lua), que la aplica en el catalogo
Automatico y exporta vistas previas de 1.600 px a _lightroom\previas\<id>\ para revisarlas.

Por foto:
  base     = la edicion de Renzo si ya la hizo (completa > basica, leida de una COPIA del catalogo
             LigaExScuola); si no, el modelo de color (color_modelo.py) + sus valores tipicos.
  encima   = lo que Renzo hace en sus mascaras, en todas: fondo con IA mas oscuro y menos saturado
             (sus mascaras: -0,3 a -0,6 EV), y el jugador con IA con mas luz, textura y claridad (mas
             luz si esta a contraluz). Pasto hacia el amarillo y mas oscuro, un toque dorado en las luces,
             vinyeta suave. Sombras un poco mas abajo: el +60 de siempre aplana; el sujeto las recupera.
Los RAW deben llamarse como en aprobadas/ del panel (02_DSC08642.ARW): el DSC busca la edicion de Renzo.
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
LR = AQUI / "_lightroom"
CATALOGO = Path(r"C:\Fotos\otro\catalogos\LigaExScuola\LigaExScuola.lrcat")
BASICOS = ("WhiteBalance", "Temperature", "Tint", "Exposure2012", "Contrast2012", "Highlights2012",
           "Shadows2012", "Whites2012", "Blacks2012", "Texture", "Clarity2012", "Dehaze", "Vibrance",
           "Saturation", "CropAngle", "CropTop", "CropLeft", "CropBottom", "CropRight", "LensProfileEnable") + tuple(
    f"{a}Adjustment{c}" for a in ("Hue", "Saturation", "Luminance")
    for c in ("Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta"))
# El mezclador de colores fue el plus de Renzo en la completa de la F3 (14/10/2026): verde tono +28 en 26 de
# 36 (lo aleja del amarillo: por eso su verde no queda oliva), y saturacion de rojo/verde/naranja/amarillo.
# Si la foto ya tiene su edicion se usa la suya; si no, sus medianas.
MEZCLADOR = {"HueAdjustmentGreen": 28, "SaturationAdjustmentGreen": 10, "SaturationAdjustmentRed": 20,
             "HueAdjustmentOrange": 2, "SaturationAdjustmentOrange": 10, "SaturationAdjustmentYellow": 10}
TIPICOS = {"Tint": 30, "Contrast2012": 15, "Highlights2012": -60, "Shadows2012": 60, "Whites2012": 12,
           "Blacks2012": -20, "Vibrance": 14, "LensProfileEnable": 1}      # medianas de sus completas
# v2 (14/10/2026, mirando la v1 al lado de las suyas): el pasto hacia el amarillo y mas oscuro quedo
# oliva y sucio, y la mascara de fondo a -0,4 EV (la IA mete el pasto en el "fondo", media foto) mas la
# vinyeta apagaban todo. Renzo edita luminoso y con el pasto verde vivo: eso se respeta.
MEJORAS = {
    "ColorGradeHighlightHue": 45, "ColorGradeHighlightSat": 10,
    "ColorGradeShadowHue": 215, "ColorGradeShadowSat": 5, "ColorGradeBlending": 50,
    "PostCropVignetteAmount": -5, "PostCropVignetteMidpoint": 50, "PostCropVignetteFeather": 70,
    "Dehaze": 4,
}
FONDO = {"local_Exposure": -0.2, "local_Saturation": -6, "local_Clarity": -5}
SUJETO = {"local_Exposure": 0.3, "local_Texture": 15, "local_Clarity": 10, "local_Shadows": 15}


def ediciones_de_renzo(fecha):
    """{DSC08104: {ajustes}} de ESA fecha (los DSC se repiten entre fechas), completa antes que basica.
    Lee una copia, nunca el original. Los -999999 (automatico sin calcular) no cuentan como valor."""
    copia = AQUI / "_color" / "liga_receta.lrcat"
    copia.write_bytes(CATALOGO.read_bytes())
    c = sqlite3.connect(copia)
    q = """SELECT f.baseName, r.absolutePath || fo.pathFromRoot, s.text FROM Adobe_imageDevelopSettings s
           JOIN Adobe_images i ON i.id_local = s.image JOIN AgLibraryFile f ON f.id_local = i.rootFile
           JOIN AgLibraryFolder fo ON fo.id_local = f.folder JOIN AgLibraryRootFolder r ON r.id_local = fo.rootFolder"""
    out = {}
    for nombre, ruta, texto in c.execute(q):
        if fecha.lower() not in ruta.lower():
            continue
        texto = texto if isinstance(texto, str) else (texto or b"").decode("utf-8", "ignore")
        aj = {}
        for k in BASICOS:
            m = re.search(rf"\b{k} = ([^,\n}}]+)", texto)
            if m:
                v = m.group(1).strip().strip('"')
                try:
                    v = float(v)
                except ValueError:
                    pass
                if v != -999999.0:
                    aj[k] = v
        prioridad = 2 if "completa" in ruta.lower() else 1
        if nombre not in out or prioridad > out[nombre][0]:
            out[nombre] = (prioridad, ruta, aj)
    return out


def lua(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(round(v, 4))
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {lua(x)}" if re.match(r"^[A-Za-z_]\w*$", k) else f"[{lua(k)}] = {lua(x)}"
                                for k, x in v.items()) + " }"
    return "{ " + ", ".join(lua(x) for x in v) + " }"


def contraluz(ruta):
    """El jugador mas oscuro que el fondo (tarde, sol detras). Mismos rasgos que el modelo de color."""
    import io
    import numpy as np
    import rawpy
    from PIL import Image
    from color_modelo import detector, rasgos_foto
    from color_pi import exif_de, lineal
    if not hasattr(contraluz, "det"):
        contraluz.det = detector()
    with rawpy.imread(str(ruta)) as raw:
        lin, recorte = lineal(raw)
        eb = [float(x) for x in raw.camera_whitebalance]
        vista = Image.open(io.BytesIO(raw.extract_thumb().data)).convert("RGB")
    vista.thumbnail((512, 512))
    r = rasgos_foto({"exif": exif_de(str(ruta)), "eb_camara": eb, "recorte": recorte},
                    lin.astype(np.float32), np.asarray(vista), contraluz.det)
    return r["jugador_vs_fondo"] < -0.3, r


def receta(carpeta, limpiar=False):
    from color_modelo import predecir
    renzo = ediciones_de_renzo(Path(carpeta).parent.name)
    rutas = sorted(p for p in Path(carpeta).iterdir() if p.suffix.lower() == ".arw")
    modelo = predecir(rutas)
    out, resumen = {}, []
    for ruta in rutas:
        dsc = ruta.stem.split("_", 1)[-1].upper()
        if dsc in renzo:
            _, origen, base = renzo[dsc]
            de = "completa" if "completa" in origen.lower() else "basica"
        else:
            base, de = dict(TIPICOS, **{"WhiteBalance": "Custom"}, **modelo[str(ruta)]), "modelo"
        aj = {k: 0 for k in BASICOS if "Adjustment" in k}   # todo el mezclador explicito: borra lo de v1/v2
        aj.update(base)
        if not any(k for k in base if "Adjustment" in k):
            aj.update(MEZCLADOR)
        for k in ("Temperature", "Exposure2012", "Tint"):   # lo que Renzo dejo en automatico
            aj.setdefault(k, modelo[str(ruta)][k])
        aj.update(MEJORAS)
        aj["WhiteBalance"] = "Custom" if "Temperature" in aj else aj.get("WhiteBalance", "As Shot")
        aj["Vibrance"] = (aj.get("Vibrance") or 0) + 4
        luz, r = contraluz(ruta)
        sujeto = dict(SUJETO)
        if luz:
            sujeto.update(local_Exposure=0.5, local_Shadows=25)
        out[str(ruta).lower()] = {"ajustes": aj, "limpiar": limpiar, "mascaras": [
            {"tipo": "background", "valores": FONDO}, {"tipo": "subject", "valores": sujeto}]}
        resumen.append(f"{ruta.name}: base {de}, {aj.get('Temperature')} K, exp {aj.get('Exposure2012')}"
                       f"{', contraluz' if luz else ''} (jugador/fondo {r['jugador_vs_fondo']:+.2f})")
    return out, resumen


def main():
    carpeta, jid = sys.argv[1], sys.argv[2]
    version = sys.argv[sys.argv.index("--version") + 1] if "--version" in sys.argv else "1"
    jid = f"{jid}-v{version}"
    datos, resumen = receta(carpeta, limpiar=version != "1")   # las versiones nuevas rehacen las mascaras
    archivo = LR / f"receta_{jid}.lua"
    archivo.write_text("return " + lua(datos) + "\n", encoding="utf-8")
    previas = LR / "previas" / jid
    linea = (f"{jid}\t{carpeta}\tIMGorgni Completa base\t\t\t\tF3 Instagram (Claude)\tsi\t{archivo}\t{previas}\n")
    cola = (LR / "cola.tsv").read_text(encoding="utf-8")
    if f"\n{jid}\t" not in "\n" + cola:
        (LR / "cola.tsv").write_text(cola + linea, encoding="utf-8")
    print("\n".join(resumen))
    print(f"receta {archivo.name} encolada como {jid}; previas -> {previas}")
    if "--abrir" in sys.argv:
        from editar_renato import abrir_y_esperar
        abrir_y_esperar([jid])


if __name__ == "__main__":
    main()
