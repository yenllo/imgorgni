r"""Elige las ~10 mejores fotos de un trabajo deportivo para Instagram y deja los recortes 4:5 listos.

    python seleccion_ig.py "Liga ExScuola\Liga ExScuola 2026-1\2026-07-11 F9 (Final)"   # uno, ya
    python seleccion_ig.py --noche     # lo que usa la tarea de las 3:30 (ver abajo)
    python seleccion_ig.py --prueba    # self-check de la matematica del recorte

Trabaja sobre CARPETAS de `Fotos\Galeria\Trabajo` (rutas relativas a esa carpeta), no sobre
eventos de photos_v2.db: al 30/9/2026 la base todavia apunta a K:\galeria\..., de antes de que
los trabajos se movieran a Fotos\Galeria\Trabajo, y los trabajos nuevos (LigaBabbo, LigaUandes)
ni estan en la base. Un "trabajo" es cualquier carpeta que tiene una subcarpeta RAW.

--noche procesa: los trabajos tocados en los ultimos DIAS_NUEVO dias que no tengan seleccion,
mas lo que haya en `_para_publicar\cola.txt` (una ruta relativa por linea; sale al terminar bien).

Embudo, del mas barato al mas caro:
  1. Autocull (local, gratis): bota lo movido/desenfocado/rafagas (~33 min por 3.000 fotos).
     OJO: su puntaje NO es editorial (su top 1 de la final del 11/7 estaba movido), por eso
     pasan TODAS las PASS al paso 2 y no solo su top.
  2. Gemini 2.5 Flash por la API gratuita: juzga las PASS de a 20 (259 tokens por foto).
  3. Claude (`claude -p`, cuota de la suscripcion): ve las <=60 mejores de Gemini y elige 10
     sin repetir jugada, con orden de publicacion, caption y recorte.
  4. Recortes 4:5 desde el JPEG original a full resolucion.

Salida en `_para_publicar\<trabajo>\`: los recortes `01_DSC....jpg`, `seleccion.json` y los
intermedios (autocull.json, gemini.json, ver\). Para rehacer un trabajo, borra su carpeta.
No escribe en photos_v2.db ni en la carpeta de fotos: solo lee de ahi.
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageOps

AQUI = Path(__file__).resolve().parent
SALIDA = AQUI / "_para_publicar"
COLA = SALIDA / "cola.txt"
CRITERIO = (AQUI / "criterio_ig.md").read_text(encoding="utf-8")
AUTOCULL_DIR = AQUI  # el paquete autocull/ vive en este proyecto
AUTOCULL_PY = Path(r"C:\Renzo\Proyectos\Autocull\venv\Scripts\python.exe")  # el venv no se mueve
ENV_GEMINI = Path(r"C:\Renzo\Estudio\.env")
MODELOS_GEMINI = ["gemini-2.5-flash", "gemini-3.5-flash"]  # el 2o si el 1o esta saturado
MODELO_CLAUDE = "sonnet"   # ponytail: sonnet cuida la cuota semanal; "opus" si el gusto no alcanza
DIAS_NUEVO = 14
ELEGIR = 10
MAX_A_CLAUDE = 60
LOTE_GEMINI = 20
PX_VER = 1280              # lo que ven los jueces
PX_RECORTE = (2160, 2700)  # 4:5, el doble de lo que muestra Instagram


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def raiz_trabajo():
    """K: si el 8TB esta por dock (o mapeado); si no, el share de la Pi por LAN.
    Recien despertada el wifi tarda en volver, por eso reintenta un rato."""
    for _ in range(36):
        for base in ("K:\\", r"\\192.168.1.62\Fotos"):
            p = Path(base) / "Fotos" / "Galeria" / "Trabajo"
            if p.is_dir():
                return p
        time.sleep(5)
    return None


def es_raw(nombre):
    return nombre.lower() == "raw"


def trabajos(base):
    """Carpetas con subcarpeta RAW. No baja mas alla de un trabajo."""
    for actual, dirs, _ in os.walk(base):
        if any(es_raw(d) for d in dirs):
            dirs[:] = []
            yield Path(actual)
        dirs[:] = [d for d in dirs if not d.startswith(".")]


def nombre_salida(base, trabajo):
    return " - ".join(trabajo.relative_to(base).parts)


def jpegs(trabajo):
    salida = []
    for actual, dirs, archivos in os.walk(trabajo):
        dirs[:] = [d for d in dirs if not es_raw(d) and not d.startswith(".")]
        salida += [str(Path(actual) / a) for a in archivos if a.lower().endswith((".jpg", ".jpeg"))]
    return sorted(salida)


# ---------------------------------------------------------------- 1. Autocull

def autocull(trabajo, carpeta):
    """Las rutas JPEG que Autocull dejo pasar. Reusa el resultado si ya corrio."""
    salida = carpeta / "autocull.json"
    d = json.loads(salida.read_text(encoding="utf-8")) if salida.exists() else {}
    if d.get("estado") != "listo":
        fotos = jpegs(trabajo)
        if not fotos:
            raise RuntimeError("el trabajo no tiene JPEG")
        log(f"Autocull sobre {len(fotos)} JPEG (esto tarda)")
        lista = carpeta / "autocull.lista.txt"
        lista.write_text("\n".join(fotos), encoding="utf-8")
        with open(carpeta / "autocull.log", "w", encoding="utf-8", errors="replace") as fh:
            subprocess.run([str(AUTOCULL_PY), str(AQUI / "autocull_runner.py"), str(lista), str(salida)],
                           cwd=str(AUTOCULL_DIR), stdout=fh, stderr=subprocess.STDOUT, check=True)
        d = json.loads(salida.read_text(encoding="utf-8"))
        if d.get("estado") != "listo":
            raise RuntimeError(f"Autocull termino en estado {d.get('estado')}: {d.get('error')}")
    pasan = [f["ruta"] for f in d["fotos"] if f["decision"] == "PASS"]
    log(f"Autocull: {len(pasan)} PASS de {len(d['fotos'])}")
    return pasan


def achicar(rutas, carpeta):
    """C001.jpg... a 1280 px para los jueces. Devuelve {id: ruta original}."""
    carpeta.mkdir(parents=True, exist_ok=True)
    mapa = {}
    for i, ruta in enumerate(rutas, 1):
        fid = f"C{i:03d}"
        destino = carpeta / f"{fid}.jpg"
        if not destino.exists():
            im = Image.open(ruta)
            im.draft("RGB", (PX_VER, PX_VER))  # decodifica el JPEG ya reducido: 42 MP pesan
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((PX_VER, PX_VER))
            im.save(destino, quality=88)
        mapa[fid] = ruta
    return mapa


# ---------------------------------------------------------------- 2. Gemini

def gemini(carpeta, ids, evento):
    from google import genai
    from google.genai import types
    key = next(l.split("=", 1)[1].strip().strip('"') for l in ENV_GEMINI.read_text().splitlines()
               if l.startswith("GEMINI_API_KEY"))
    cliente = genai.Client(api_key=key)  # en variable: si es temporal se cierra antes de la llamada
    veredictos = {}
    for i in range(0, len(ids), LOTE_GEMINI):
        lote = ids[i:i + LOTE_GEMINI]
        partes = [CRITERIO, f"Trabajo: {evento}. Para CADA foto responde SOLO un objeto JSON "
                  '{"<id>": {"veredicto": "si"|"no", "nota": 1-10, "motivo": "frase corta"}} '
                  f"con estas claves: {', '.join(lote)}."]
        for fid in lote:
            partes += [f"Foto {fid}:", types.Part.from_bytes(data=(carpeta / f"{fid}.jpg").read_bytes(),
                                                             mime_type="image/jpeg")]
        for intento in range(8):
            modelo = MODELOS_GEMINI[intento % len(MODELOS_GEMINI)]
            try:
                r = cliente.models.generate_content(
                    model=modelo, contents=partes,
                    config=types.GenerateContentConfig(response_mime_type="application/json"))
                veredictos.update({k: v for k, v in json.loads(r.text).items() if k in lote})
                break
            except Exception as e:  # 429 cuota por minuto / 503 saturado: esperar y rotar modelo
                log(f"  Gemini {modelo} fallo ({str(e)[:80]}), reintento")
                time.sleep(30)
        else:
            log(f"  lote {lote[0]}..{lote[-1]} sin veredicto, sigue sin el")
        log(f"Gemini {min(i + LOTE_GEMINI, len(ids))}/{len(ids)}")
        time.sleep(7)  # la capa gratuita corta por pedidos por minuto
    return veredictos


# ---------------------------------------------------------------- 3. Claude

def extraer_json(texto):
    return json.loads(texto[texto.index("{"):texto.rindex("}") + 1])


def claude(carpeta, ids, evento):
    prompt = f"""{CRITERIO}
Trabajo: {evento}.
En esta carpeta hay {len(ids)} fotos candidatas: {', '.join(i + '.jpg' for i in ids)}.
Lee TODAS con la herramienta Read, varias en paralelo por turno para no gastar turnos.

Elige hasta {ELEGIR} para publicar (menos si no hay {ELEGIR} buenas de verdad), con estas reglas:
- Nunca dos fotos de la misma jugada o del mismo momento: quedate con la mejor.
- Variedad: distintos jugadores/equipos y tipos de momento (accion, arquero, celebracion, historia).
- Ordenalas en el orden en que conviene publicarlas (la mas fuerte primero).
- Para cada una define un recorte vertical 4:5: centro_x y centro_y (0 a 1, sobre la foto completa)
  y alto (fraccion del alto de la foto que ocupa el recorte, entre 0.35 y 1; mas chico = mas zoom).
  Los jugadores salen chicos en cuadro: casi siempre conviene zoom.
- caption: una linea en español de Chile (tuteo chileno, nunca voseo), sin hashtags.

Responde SOLO este JSON, sin texto antes ni despues:
{{"elegidas": [{{"id": "C001", "motivo": "...", "centro_x": 0.5, "centro_y": 0.5, "alto": 0.6, "caption": "..."}}]}}"""
    exe = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude.exe")
    r = subprocess.run([exe, "-p", prompt, "--allowedTools", "Read", "--output-format", "json",
                        "--model", MODELO_CLAUDE], cwd=str(carpeta), capture_output=True,
                       stdin=subprocess.DEVNULL, text=True, encoding="utf-8", timeout=3600)
    if r.returncode:
        raise RuntimeError(f"claude -p salio con {r.returncode}: {r.stdout[-400:]} {r.stderr[-400:]}")
    salida = json.loads(r.stdout)
    elegidas = extraer_json(salida["result"])["elegidas"]
    return [e for e in elegidas if e.get("id") in ids], salida.get("usage")


# ---------------------------------------------------------------- 4. Recortes

def caja_4x5(ancho, alto_img, cx, cy, alto):
    """Caja (x0, y0, x1, y1) 4:5 centrada en (cx, cy), corrida para no salirse de la foto."""
    h = alto_img * min(max(alto, 0.2), 1.0)
    w = h * 4 / 5
    if w > ancho:  # foto mas angosta que 4:5: manda el ancho
        w, h = ancho, ancho * 5 / 4
    x0 = min(max(cx * ancho - w / 2, 0), ancho - w)
    y0 = min(max(cy * alto_img - h / 2, 0), alto_img - h)
    return round(x0), round(y0), round(x0 + w), round(y0 + h)


def recortar(ruta, e, destino):
    im = ImageOps.exif_transpose(Image.open(ruta)).convert("RGB")
    im = im.crop(caja_4x5(*im.size, float(e["centro_x"]), float(e["centro_y"]), float(e["alto"])))
    im.thumbnail(PX_RECORTE, Image.LANCZOS)
    im.save(destino, quality=92)


# ---------------------------------------------------------------- orquesta

def procesar(base, trabajo):
    evento = nombre_salida(base, trabajo)
    carpeta = SALIDA / evento
    if (carpeta / "seleccion.json").exists():
        log(f"{evento}: ya tiene seleccion, salto (borra la carpeta para rehacer)")
        return
    log(f"=== {evento}")
    carpeta.mkdir(parents=True, exist_ok=True)
    mapa = achicar(autocull(trabajo, carpeta), carpeta / "ver")
    (carpeta / "mapa.json").write_text(json.dumps(mapa, ensure_ascii=False, indent=1), encoding="utf-8")

    archivo_g = carpeta / "gemini.json"
    vg = json.loads(archivo_g.read_text(encoding="utf-8")) if archivo_g.exists() else {}
    faltan = [i for i in mapa if i not in vg]
    if faltan:
        vg.update(gemini(carpeta / "ver", faltan, evento))
        archivo_g.write_text(json.dumps(vg, ensure_ascii=False, indent=1), encoding="utf-8")
    orden = sorted(vg, key=lambda i: (vg[i].get("veredicto") != "si", -int(vg[i].get("nota", 0))))
    si = sum(vg[i].get("veredicto") == "si" for i in vg)
    a_claude = orden[:min(MAX_A_CLAUDE, max(si, 30))]  # si Gemini fue tacaño, igual le paso 30
    log(f"Gemini: {si} si de {len(vg)}; a Claude van {len(a_claude)}")

    elegidas, uso = claude(carpeta / "ver", a_claude, evento)
    log(f"Claude eligio {len(elegidas)}; uso: {uso}")
    for n, e in enumerate(elegidas, 1):
        e["original"] = mapa[e["id"]]
        e["archivo"] = f"{n:02d}_{Path(e['original']).stem}.jpg"
        recortar(e["original"], e, carpeta / e["archivo"])
    (carpeta / "seleccion.json").write_text(json.dumps(
        {"trabajo": evento, "elegidas": elegidas, "uso_claude": uso, "gemini_si": si,
         "autocull_pass": len(mapa)}, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"listo: {carpeta}")


def prueba():
    assert caja_4x5(1000, 800, 0.5, 0.5, 1.0) == (180, 0, 820, 800)       # 4:5 a todo el alto
    assert caja_4x5(1000, 800, 0.0, 0.0, 0.5) == (0, 0, 320, 400)         # se pega a la esquina
    assert caja_4x5(1000, 800, 1.0, 1.0, 0.5) == (680, 400, 1000, 800)    # no se sale por abajo/derecha
    assert caja_4x5(600, 1000, 0.5, 0.5, 1.0) == (0, 125, 600, 875)       # vertical angosta: manda el ancho
    x0, y0, x1, y1 = caja_4x5(7952, 5304, 0.3, 0.7, 0.45)
    assert abs((x1 - x0) / (y1 - y0) - 0.8) < 0.01
    assert extraer_json('bla {"a": {"b": 1}} fin') == {"a": {"b": 1}}
    print("ok")


def main():
    args = sys.argv[1:]
    if args == ["--prueba"]:
        return prueba()
    # Que Windows no la vuelva a dormir a mitad de camino: despierta por temporizador y
    # se suspende sola a los pocos minutos si nadie la retiene.
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    base = raiz_trabajo()
    if not base:
        return log("no llego a Fotos\\Galeria\\Trabajo (fuera de casa o la Pi apagada), no hago nada")
    SALIDA.mkdir(exist_ok=True)
    cola = [l.strip() for l in COLA.read_text(encoding="utf-8").splitlines() if l.strip()] if COLA.exists() else []
    if args == ["--noche"]:
        limite = time.time() - DIAS_NUEVO * 86400
        todo = [t for t in trabajos(base) if t.stat().st_mtime > limite] + [base / c for c in cola]
    else:
        todo = [base / a for a in args]
    for trabajo in dict.fromkeys(todo):
        try:
            if not trabajo.is_dir():
                raise RuntimeError(f"no existe {trabajo}")
            procesar(base, trabajo)
            rel = str(trabajo.relative_to(base))
            if rel in cola:  # sale de la cola recien cuando termino bien
                cola.remove(rel)
                COLA.write_text("".join(c + "\n" for c in cola), encoding="utf-8")
        except Exception as e:
            log(f"{trabajo} fallo: {e}")


if __name__ == "__main__":
    main()
