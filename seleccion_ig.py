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

Embudo, del mas barato al mas caro. Todo Claude, sin agy ni Gemini (pedido de Renzo, 1/10/2026):
  1. Autocull (local, gratis): bota lo movido/desenfocado/rafagas (~25 min por 4.000 fotos).
     OJO: su puntaje NO es editorial, por eso pasan TODAS las PASS al paso 2 y no solo su top.
  2. Filtro Claude Haiku sobre HOJAS DE CONTACTO de 9 fotos (~240 tokens por foto en vez de
     ~1.500 foto por foto): nota gruesa a todas las PASS.
  3. Claude Sonnet ve las <=60 mejores a 1280 px y elige 10 sin repetir jugada, con orden de
     publicacion, caption y recorte. Lee `data/apuntes_renato.md` (lo que aprendio de lo que elige
     Renato, ver aprender_apuntes.py) y deja un apunte de esta seleccion en `_para_publicar\bitacora.md`.
  4. Recortes 4:5 desde el JPEG original a full resolucion.

Salida en `_para_publicar\<trabajo>\`: los recortes `01_DSC....jpg`, `seleccion.json` y los
intermedios (autocull.json, filtro.json, ver\, hojas\). Para rehacer un trabajo, borra su carpeta.
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
APUNTES = AQUI / "data" / "apuntes_renato.md"
BITACORA = SALIDA / "bitacora.md"
SISTEMA = ("Eres editor de fotografia deportiva. Miras imagenes con la herramienta Read y respondes "
           "solo con JSON valido, sin texto antes ni despues.")
VER = "ver_id"             # fotos a 1280 px CON su id escrito encima (ver achicar); "ver" eran las sin id
MODELO_FILTRO = "haiku"    # nota gruesa sobre hojas de contacto: barato
MODELO_CLAUDE = "sonnet"   # ponytail: sonnet cuida la cuota semanal; "opus" si el gusto no alcanza
DIAS_NUEVO = 14
ELEGIR = 10
MAX_A_CLAUDE = 60
POR_HOJA = 9               # 3x3
HOJAS_POR_LLAMADA = 6      # ~54 fotos por claude -p: reparte el costo fijo de cada llamada
PX_VER = 1280              # lo que ve el juez final
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


def rotular(im, texto, tam=40):
    """Escribe el id ENCIMA de la foto (esquina superior izquierda, amarillo sobre negro).

    Por que: en la prueba del 1/10 Claude leyo 60 fotos por nombre de archivo y en ~6 de las 10
    elegidas el motivo y el recorte eran de OTRA foto (el mismo error de agy en la muestra). Con
    el id en los pixeles no tiene que recordar que archivo era cual: lo lee.
    """
    from PIL import ImageDraw, ImageFont
    try:
        fuente = ImageFont.truetype("arialbd.ttf", tam)
    except OSError:
        fuente = ImageFont.load_default()
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, int(tam * 3.2), int(tam * 1.4)), fill="black")
    d.text((8, 4), texto, fill="yellow", font=fuente)
    return im


def achicar(rutas, carpeta):
    """C001.jpg... a 1280 px para los jueces, con el id escrito encima. Devuelve {id: ruta original}."""
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
            rotular(im, fid).save(destino, quality=88)
        mapa[fid] = ruta
    return mapa


# ---------------------------------------------------------------- Claude por linea de comando

def extraer_json(texto):
    # strict=False: acepta saltos de linea crudos dentro de los strings (el modelo los mete a veces).
    return json.loads(texto[texto.index("{"):texto.rindex("}") + 1], strict=False)


def claude_json(prompt, carpeta, modelo):
    """`claude -p` en *carpeta*, devuelve (JSON de la respuesta, uso de tokens)."""
    texto, uso = claude_texto(prompt, carpeta, modelo)
    return extraer_json(texto), uso


def claude_texto(prompt, carpeta, modelo):
    """`claude -p` en *carpeta* (solo puede leer ahi), devuelve (texto de la respuesta, uso de tokens).
    Usa la suscripcion: en la tarea programada, por CLAUDE_CODE_OAUTH_TOKEN (claude setup-token)."""
    exe = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude.exe")
    # Sin esto cada llamada cargaba ~50 mil tokens antes de la primera foto (prompt de Claude Code,
    # todas las herramientas, MCP, skills): medido en la prueba del 1/10. Solo hace falta Read.
    r = subprocess.run([exe, "-p", prompt, "--model", modelo, "--output-format", "json",
                        "--tools", "Read", "--allowedTools", "Read", "--strict-mcp-config",
                        "--disable-slash-commands", "--system-prompt", SISTEMA],
                       cwd=str(carpeta), capture_output=True,
                       stdin=subprocess.DEVNULL, text=True, encoding="utf-8", timeout=3600)
    if r.returncode:
        raise RuntimeError(f"claude -p salio con {r.returncode}: {r.stdout[-400:]} {r.stderr[-400:]}")
    salida = json.loads(r.stdout)
    return salida["result"], salida.get("usage")


def apuntes():
    """Lo que Claude aprendio de las selecciones de Renato (aprender_apuntes.py), si ya existe."""
    if APUNTES.exists():
        return "\nApuntes de lo que elige Renato (cliente de la Liga ExScuola), aprendidos de sus selecciones " \
               "pasadas. Usalos como referencia de gusto, no como regla ciega:\n" + APUNTES.read_text(encoding="utf-8")
    return ""


# ---------------------------------------------------------------- 2. Filtro sobre hojas de contacto

def hoja(rutas, destino, etiquetas, titulo="", celda=(520, 347)):
    """Hoja de contacto 3x3 (1560x~1040, ~2.200 tokens de imagen) con la etiqueta de cada foto."""
    from PIL import ImageDraw, ImageFont
    cw, ch = celda
    filas = (len(rutas) + 2) // 3
    alto_tit = 40 if titulo else 0
    lienzo = Image.new("RGB", (cw * 3, ch * filas + alto_tit), "black")
    dib = ImageDraw.Draw(lienzo)
    try:
        fuente = ImageFont.truetype("arialbd.ttf", 30)
    except OSError:
        fuente = ImageFont.load_default()
    if titulo:
        dib.text((10, 4), titulo, fill="yellow", font=fuente)
    for i, (ruta, et) in enumerate(zip(rutas, etiquetas)):
        im = Image.open(ruta)
        im.draft("RGB", (cw * 2, ch * 2))
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((cw - 4, ch - 4))
        x, y = (i % 3) * cw, alto_tit + (i // 3) * ch
        lienzo.paste(im, (x + (cw - im.width) // 2, y + (ch - im.height) // 2))
        dib.rectangle((x + 2, y + 2, x + 92, y + 40), fill="black")
        dib.text((x + 6, y + 4), et, fill="yellow", font=fuente)
    lienzo.save(destino, quality=85)


def filtro(carpeta, ids, evento):
    """Nota gruesa de Claude Haiku a TODAS las candidatas, mirando hojas de 9."""
    hojas_dir = carpeta / "hojas"
    hojas_dir.mkdir(exist_ok=True)
    grupos = [ids[i:i + POR_HOJA] for i in range(0, len(ids), POR_HOJA)]
    nombres = []
    for g in grupos:
        nombre = f"H_{g[0]}.jpg"
        if not (hojas_dir / nombre).exists():
            hoja([carpeta / VER / f"{i}.jpg" for i in g], hojas_dir / nombre, g)
        nombres.append((nombre, g))
    veredictos = {}
    for k in range(0, len(nombres), HOJAS_POR_LLAMADA):
        tanda = nombres[k:k + HOJAS_POR_LLAMADA]
        ids_tanda = [i for _, g in tanda for i in g]
        prompt = f"""{CRITERIO}{apuntes()}
Trabajo: {evento}. Esto es un FILTRO grueso: despues otro juez mira las mejores en grande.
Lee con Read estas hojas de contacto (cada celda tiene su id en amarillo): {', '.join(n for n, _ in tanda)}.
Para CADA id ({', '.join(ids_tanda)}) responde SOLO un JSON
{{"<id>": {{"veredicto": "si"|"no", "nota": 1-10, "motivo": "frase corta"}}}} sin texto antes ni despues."""
        try:
            datos, _uso = claude_json(prompt, hojas_dir, MODELO_FILTRO)
            veredictos.update({i: v for i, v in datos.items() if i in ids_tanda})
        except Exception as e:  # una tanda mala no tumba el trabajo: esas fotos quedan sin nota
            log(f"  filtro: tanda {ids_tanda[0]}.. fallo: {str(e)[:150]}")
        log(f"filtro {min(k + HOJAS_POR_LLAMADA, len(nombres)) * POR_HOJA}/{len(ids)}")
    return veredictos


# ---------------------------------------------------------------- 3. Eleccion final

_ID_EN_IMAGEN = ("Cada imagen tiene su id escrito en amarillo en la esquina superior izquierda: ese es su "
                 "id. Usa SIEMPRE el id que lees en la imagen, no el que recuerdes del nombre de archivo.")


def claude(carpeta, ids, evento):
    """Paso A: elige y ordena. Solo ids y por que; el recorte lo decide detallar() mirando solo a esas."""
    prompt = f"""{CRITERIO}{apuntes()}
Trabajo: {evento}.
En esta carpeta hay {len(ids)} fotos candidatas: {', '.join(i + '.jpg' for i in ids)}.
Lee TODAS con la herramienta Read, varias en paralelo por turno para no gastar turnos.
{_ID_EN_IMAGEN}

Elige hasta {ELEGIR} para publicar (menos si no hay {ELEGIR} buenas de verdad), con estas reglas:
- Nunca dos fotos de la misma jugada o del mismo momento: quedate con la mejor.
- Variedad: distintos jugadores/equipos y tipos de momento (accion, arquero, celebracion, historia).
- Ordenalas en el orden en que conviene publicarlas (la mas fuerte primero).
- motivo: una frase de por que esa foto, describiendo lo que SE VE en ella.
- apunte: 2 a 4 lineas para tu bitacora: que hizo fuerte (o flojo) este trabajo, que patron viste,
  que harias distinto la proxima vez. Queda anotado para las siguientes selecciones.

Responde SOLO este JSON:
{{"apunte": "...", "elegidas": [{{"id": "C001", "motivo": "..."}}]}}"""
    datos, uso = claude_json(prompt, carpeta, MODELO_CLAUDE)
    return [e for e in datos["elegidas"] if e.get("id") in ids], datos.get("apunte", ""), uso


def detallar(carpeta, elegidas, evento):
    """Paso B: mira SOLO las elegidas (pocas, sin confundirse) y decide recorte y caption de cada una."""
    ids = [e["id"] for e in elegidas]
    prompt = f"""Trabajo: {evento}. Estas {len(ids)} fotos ya fueron elegidas para Instagram:
{', '.join(i + '.jpg' for i in ids)}. Lee cada una con Read. {_ID_EN_IMAGEN}

Para cada id:
- vista: una frase con lo que se ve en ESA imagen (sirve para verificar que no la confundiste).
- recorte vertical 4:5 sobre la foto completa: centro_x y centro_y (0 a 1) y alto (fraccion del alto
  de la foto, entre 0.35 y 1; mas chico = mas zoom). Centra la accion (jugador con la pelota, el
  arquero, la celebracion). Los jugadores salen chicos en cuadro: casi siempre conviene zoom, PERO
  antes de responder verifica que dentro del recorte quepan COMPLETOS la cabeza del protagonista y la
  pelota (y los pies si esta rematando). Si no caben, sube alto o mueve el centro: una cabeza cortada
  arruina la foto (paso en la primera noche, 2 de 10).
- caption: una linea en español de Chile (tuteo chileno, nunca voseo), sin hashtags.

Responde SOLO este JSON:
{{"<id>": {{"vista": "...", "centro_x": 0.5, "centro_y": 0.5, "alto": 0.6, "caption": "..."}}}}"""
    datos, uso = claude_json(prompt, carpeta, MODELO_CLAUDE)
    for e in elegidas:
        e.update(datos.get(e["id"], {}))
    return [e for e in elegidas if "centro_x" in e], uso


def anotar(evento, elegidas, apunte):
    """Bitacora legible de cada seleccion: que se eligio y por que."""
    lineas = [f"\n## {time.strftime('%Y-%m-%d %H:%M')} — {evento}\n", (apunte or "").strip(), ""]
    lineas += [f"{n}. `{e['archivo']}` — {e.get('vista') or e.get('motivo', '')}" for n, e in enumerate(elegidas, 1)]
    with open(BITACORA, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lineas) + "\n")


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
    mapa = achicar(autocull(trabajo, carpeta), carpeta / VER)
    (carpeta / "mapa.json").write_text(json.dumps(mapa, ensure_ascii=False, indent=1), encoding="utf-8")

    archivo_f = carpeta / "filtro.json"
    vf = json.loads(archivo_f.read_text(encoding="utf-8")) if archivo_f.exists() else {}
    faltan = [i for i in mapa if i not in vf]
    if faltan:
        vf.update(filtro(carpeta, faltan, evento))
        archivo_f.write_text(json.dumps(vf, ensure_ascii=False, indent=1), encoding="utf-8")
    orden = sorted(vf, key=lambda i: (vf[i].get("veredicto") != "si", -int(vf[i].get("nota", 0))))
    si = sum(vf[i].get("veredicto") == "si" for i in vf)
    a_claude = orden[:min(MAX_A_CLAUDE, max(si, 30))]  # si el filtro fue tacaño, igual le paso 30
    log(f"filtro: {si} si de {len(vf)}; a la eleccion final van {len(a_claude)}")

    elegidas, apunte, uso = claude(carpeta / VER, a_claude, evento)
    log(f"Claude eligio {len(elegidas)}")
    elegidas, uso_b = detallar(carpeta / VER, elegidas, evento)
    uso = {"eleccion": uso, "recortes": uso_b}
    log(f"recortes para {len(elegidas)}; uso: {json.dumps(uso)[:300]}")
    for n, e in enumerate(elegidas, 1):
        e["original"] = mapa[e["id"]]
        e["archivo"] = f"{n:02d}_{Path(e['original']).stem}.jpg"
        recortar(e["original"], e, carpeta / e["archivo"])
    (carpeta / "seleccion.json").write_text(json.dumps(
        {"trabajo": evento, "apunte": apunte, "elegidas": elegidas, "uso_claude": uso, "filtro_si": si,
         "autocull_pass": len(mapa)}, ensure_ascii=False, indent=1), encoding="utf-8")
    anotar(evento, elegidas, apunte)
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
                # 1/10 05:55: un corte de red de segundos tumbo los 4 trabajos que quedaban en el mismo
                # instante. raiz_trabajo() espera hasta 3 min a que vuelva el share antes de rendirse.
                raiz_trabajo()
            if not trabajo.is_dir():
                raise RuntimeError(f"no existe {trabajo}")
            try:
                procesar(base, trabajo)
            except OSError as e:  # el share se cayo a mitad de camino: procesar retoma lo ya guardado
                log(f"{trabajo}: {e}; espero al NAS y reintento una vez")
                raiz_trabajo()
                procesar(base, trabajo)
            rel = str(trabajo.relative_to(base))
            if rel in cola:  # sale de la cola recien cuando termino bien
                cola.remove(rel)
                COLA.write_text("".join(c + "\n" for c in cola), encoding="utf-8")
        except Exception as e:
            log(f"{trabajo} fallo: {e}")


if __name__ == "__main__":
    main()
