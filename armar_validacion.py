"""
Arma un set de validación para medir la precisión de la clasificación.

Selecciona una muestra estratificada de fotos reales, genera miniaturas y una
página HTML autónoma para etiquetarlas a mano en el navegador. Después,
`puntuar_validacion.py` compara tus etiquetas contra cada modelo.

La página NO muestra lo que predijo el modelo mientras etiquetás: así tu juicio
no queda sesgado por la respuesta de la máquina.

    python armar_validacion.py                      # ~140 fotos del ensayo
    python armar_validacion.py --origen F:/fotos    # muestrear de un disco
    python armar_validacion.py --n 200
"""
import argparse
import base64
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import cv2

sys.path.insert(0, ".")
from utils_images import load_image_array, is_image_file, SUPPORTED_RAW_EXTS
from utils_analisis import scene_to_category, clasificar_dispositivo, es_no_foto

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CATEGORIAS = [
    "Fotos personas", "Fotos animales", "Fotos paisajes", "Fotos eventos",
    "Fotos comida", "Fotos deportes", "Fotos autos", "Fotos plantas",
    "Fotos Astro", "Fotos escolares", "Fotos descartes", "Fotos varias",
    "_No son fotos",
]


def muestra_estratificada(db_path: str, n: int) -> list:
    """Muestra del ensayo con cobertura de todas las categorías predichas."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    filas = conn.execute("""
        SELECT f.file_path, f.camera_model, pf.scene_tags, pf.quality_json
        FROM files f JOIN photo_features pf ON f.id = pf.photo_id
    """).fetchall()
    conn.close()

    por_cat = defaultdict(list)
    descartadas_no_camara = 0
    for fp, cm, sj, qj in filas:
        if not Path(fp).exists():
            continue
        try:
            escenas = json.loads(sj or "[]")
            q = json.loads(qj or "{}")
        except json.JSONDecodeError:
            continue
        # Solo fotos de cámara: son las únicas que el programa organiza, así que
        # son las únicas cuya clasificación tiene sentido validar.
        if clasificar_dispositivo(cm, fp) != "camara":
            descartadas_no_camara += 1
            continue
        if es_no_foto(cm, fp, q):
            cat = "_No son fotos"
        else:
            cat = scene_to_category(escenas, q, clasificar_dispositivo(cm, fp))
        por_cat[cat].append(fp)
    print(f"  ({descartadas_no_camara} fotos no-cámara excluidas del set)")

    random.seed(42)
    por_categoria = max(4, n // max(len(por_cat), 1))
    muestra = []
    for cat, fps in por_cat.items():
        random.shuffle(fps)
        muestra.extend(fps[:por_categoria])
    random.shuffle(muestra)
    return muestra[:n]


def muestra_de_disco(origen: str, n: int) -> list:
    """Muestra directa de un directorio (para validar sobre fotos sin indexar)."""
    todas = [str(p) for p in Path(origen).rglob("*") if p.is_file() and is_image_file(str(p))]
    random.seed(42)
    random.shuffle(todas)
    # Sobre-muestrear para descartar las que no se puedan decodificar
    return todas[:n * 2]


def thumbnail_datauri(fp: str, lado: int = 320) -> str:
    """Miniatura JPEG como data URI base64 (para HTML autónomo)."""
    img = load_image_array(fp)
    h, w = img.shape[:2]
    escala = lado / max(h, w)
    if escala < 1:
        img = cv2.resize(img, (int(w * escala), int(h * escala)),
                         interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 78])
    if not ok:
        raise ValueError("no se pudo codificar")
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode()


def generar_html(items: list, salida: Path):
    cats_js = json.dumps(CATEGORIAS, ensure_ascii=False)
    datos_js = json.dumps(items, ensure_ascii=False)
    html = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Validación de categorías</title>
<style>
 :root{color-scheme:light dark}
 *{box-sizing:border-box}
 body{font-family:system-ui,sans-serif;margin:0;background:#111;color:#eee}
 header{position:sticky;top:0;background:#1a1a1a;padding:12px 16px;border-bottom:1px solid #333;z-index:10}
 .barra{height:8px;background:#333;border-radius:4px;overflow:hidden;margin-top:8px}
 .barra>div{height:100%;background:#4ade80;width:0;transition:width .2s}
 #stage{display:flex;flex-direction:column;align-items:center;padding:16px;gap:12px}
 img{max-width:min(90vw,640px);max-height:60vh;border-radius:8px;background:#000}
 .cats{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;max-width:800px}
 button.cat{padding:10px 14px;border:1px solid #444;background:#222;color:#eee;border-radius:8px;cursor:pointer;font-size:15px}
 button.cat:hover{border-color:#4ade80;background:#2a2a2a}
 button.cat b{color:#4ade80;margin-right:6px}
 .nav{display:flex;gap:8px;align-items:center;margin-top:8px}
 .nav button{padding:8px 16px;border:1px solid #444;background:#222;color:#eee;border-radius:8px;cursor:pointer}
 #exportar{background:#4ade80;color:#000;font-weight:bold;border:none;padding:10px 20px;border-radius:8px;cursor:pointer}
 .hint{color:#888;font-size:13px}
 .filepath{color:#666;font-size:12px;word-break:break-all;max-width:640px;text-align:center}
</style></head><body>
<header>
 <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
  <div><b id="pos">1</b> / <span id="total">0</span> &nbsp;·&nbsp; <span id="hechas">0</span> etiquetadas</div>
  <button id="exportar">💾 Exportar etiquetas (JSON)</button>
 </div>
 <div class="barra"><div id="prog"></div></div>
 <div class="hint">Elegí la categoría a la que DEBERÍA ir la foto. Teclas 1-9 y letras. ← → para navegar. Se guarda solo en tu navegador.</div>
</header>
<div id="stage">
 <img id="foto" alt="">
 <div class="filepath" id="ruta"></div>
 <div class="cats" id="cats"></div>
 <div class="nav">
  <button onclick="ir(-1)">← Anterior</button>
  <button onclick="saltar()">Saltar (no sé)</button>
  <button onclick="ir(1)">Siguiente →</button>
 </div>
</div>
<script>
const CATS=__CATS__, DATOS=__DATOS__;
const KEYS="123456789qwertyu".split("");
let i=0, etiquetas={};
try{etiquetas=JSON.parse(localStorage.getItem("validacion_fotos")||"{}")}catch(e){}
const $=id=>document.getElementById(id);
$("total").textContent=DATOS.length;

function pintarCats(){
 $("cats").innerHTML="";
 CATS.forEach((c,idx)=>{
  const b=document.createElement("button");
  b.className="cat";
  b.innerHTML="<b>"+(KEYS[idx]||"").toUpperCase()+"</b>"+c;
  const actual=etiquetas[DATOS[i].id];
  if(actual===c) b.style.borderColor="#4ade80", b.style.background="#1e3a2a";
  b.onclick=()=>elegir(c);
  $("cats").appendChild(b);
 });
}
function mostrar(){
 const d=DATOS[i];
 $("foto").src=d.img; $("ruta").textContent=d.name;
 $("pos").textContent=i+1;
 $("hechas").textContent=Object.keys(etiquetas).length;
 $("prog").style.width=(100*Object.keys(etiquetas).length/DATOS.length)+"%";
 pintarCats();
}
function elegir(c){
 etiquetas[DATOS[i].id]=c;
 localStorage.setItem("validacion_fotos",JSON.stringify(etiquetas));
 if(i<DATOS.length-1){i++;mostrar()}else mostrar();
}
function saltar(){ if(i<DATOS.length-1){i++;mostrar()} }
function ir(d){ i=Math.max(0,Math.min(DATOS.length-1,i+d)); mostrar(); }
document.onkeydown=e=>{
 if(e.key==="ArrowRight")return ir(1);
 if(e.key==="ArrowLeft")return ir(-1);
 const idx=KEYS.indexOf(e.key.toLowerCase());
 if(idx>=0 && idx<CATS.length) elegir(CATS[idx]);
};
$("exportar").onclick=()=>{
 const blob=new Blob([JSON.stringify(etiquetas,null,1)],{type:"application/json"});
 const a=document.createElement("a");
 a.href=URL.createObjectURL(blob); a.download="etiquetas_validacion.json"; a.click();
};
mostrar();
</script></body></html>"""
    html = html.replace("__CATS__", cats_js).replace("__DATOS__", datos_js)
    salida.write_text(html, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="ensayo/photos.db")
    ap.add_argument("--origen", default=None, help="Muestrear de un disco en vez del ensayo")
    ap.add_argument("--n", type=int, default=140)
    ap.add_argument("--out", default="validacion")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if args.origen:
        rutas = muestra_de_disco(args.origen, args.n)
    else:
        rutas = muestra_estratificada(args.db, args.n)

    print(f"Generando miniaturas de {len(rutas)} fotos...")
    items = []
    for fp in rutas:
        if len(items) >= args.n:
            break
        try:
            uri = thumbnail_datauri(fp)
        except Exception:
            continue
        items.append({"id": str(len(items)), "path": fp,
                      "name": Path(fp).name, "img": uri})

    # Guardar el mapa id->ruta para que el scorer sepa qué archivo es cada uno
    mapa = {it["id"]: it["path"] for it in items}
    (out / "mapa_validacion.json").write_text(
        json.dumps(mapa, ensure_ascii=False, indent=1), encoding="utf-8")

    html_path = out / "etiquetar.html"
    generar_html(items, html_path)

    mb = html_path.stat().st_size / 1e6
    print(f"✅ {len(items)} fotos listas ({mb:.1f} MB)")
    print(f"\n1. Abrí en el navegador:  {html_path.resolve()}")
    print("2. Etiquetá cada foto (teclas 1-9 y letras, ← →)")
    print("3. Clic en 'Exportar' -> guardá etiquetas_validacion.json en la carpeta 'validacion/'")
    print("4. Corré:  python puntuar_validacion.py")


if __name__ == "__main__":
    main()
