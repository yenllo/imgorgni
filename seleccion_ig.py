r"""Elige las ~40 mejores fotos de un trabajo deportivo para Instagram y las copia completas.

    python seleccion_ig.py "Liga ExScuola\Liga ExScuola 2026-1\2026-07-11 F9 (Final)"   # uno, ya
    python seleccion_ig.py --noche     # lo que usa la tarea de las 3:30 (ver abajo)
    python seleccion_ig.py --renato    # solo el top de las selecciones terminadas de Renato
    python seleccion_ig.py --prueba    # self-check

Trabaja sobre CARPETAS de `Fotos\Galeria\Trabajo` (rutas relativas a esa carpeta), no sobre
eventos de photos_v2.db: al 30/9/2026 la base todavia apunta a K:\galeria\..., de antes de que
los trabajos se movieran a Fotos\Galeria\Trabajo, y los trabajos nuevos (LigaBabbo, LigaUandes)
ni estan en la base. Un "trabajo" es cualquier carpeta que tiene una subcarpeta RAW.

--noche procesa las FECHAS NUEVAS: carpetas de trabajo que no estan en `_para_publicar\vistos.json`
y que ya terminaron de copiarse (ninguna carpeta del trabajo cambio en ESTABLE_HORAS), mas lo que
haya en `_para_publicar\cola.txt` (una ruta relativa por linea; sale al terminar bien). Renzo saca
fotos sabados y a veces jueves/domingos: sin fecha nueva no se analiza nada (1/10/2026). Lo corren
dos tareas (registrar_tarea_ig.ps1): cada hora con la notebook prendida, y despertandola vie/dom/lun
3:30. Un candado evita que dos corridas procesen lo mismo a la vez.

Embudo, del mas barato al mas caro. Todo Claude, sin agy ni Gemini (pedido de Renzo, 1/10/2026):
  1. Autocull (local, gratis): bota lo movido/desenfocado/rafagas (~25 min por 4.000 fotos).
     OJO: su puntaje NO es editorial, por eso pasan TODAS las PASS al paso 2 y no solo su top.
  2. Filtro Claude Haiku sobre HOJAS DE CONTACTO de 9 fotos (~240 tokens por foto en vez de
     ~1.500 foto por foto): nota gruesa a todas las PASS.
  3. Claude Sonnet ve las <=80 mejores a 1280 px y elige ELEGIR (40) sin repetir jugada, en orden de
     publicacion. Lee `data/apuntes_renato.md` (lo que aprendio de lo que elige Renato, ver
     aprender_apuntes.py) y deja un apunte de esta seleccion en `_para_publicar\bitacora.md`.
     Una segunda pasada mira solo las elegidas y escribe que se ve (para verificar) y el caption.
  4. Copia el JPEG ORIGINAL COMPLETO, sin recortar ni recomprimir: Renzo sube horizontal y
     recorta/edita el (pedido del 1/10/2026; antes salian recortes 4:5).

Salida en `_para_publicar\<trabajo>\`: las fotos `01_DSC....jpg`, `seleccion.json` y los
intermedios (autocull.json, filtro.json, ver\, hojas\). Para rehacer un trabajo, borra su carpeta.
No escribe en photos_v2.db ni en la carpeta de fotos: solo lee de ahi.

Ademas, cada corrida --noche mira en la Pi si Renato (cliente de la Liga ExScuola) apreto "Termine mi
seleccion" en alguna galeria: de sus ~100 fotos Sonnet elige las TOP_RENATO mejores para Instagram y
quedan en renato.json, segunda seccion de /panel/revision de esa fecha (pedido de Renzo, 7/10/2026).
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

AQUI = Path(__file__).resolve().parent
SALIDA = AQUI / "_para_publicar"
COLA = SALIDA / "cola.txt"
CRITERIO = (AQUI / "criterio_ig.md").read_text(encoding="utf-8")
AUTOCULL_DIR = AQUI  # el paquete autocull/ vive en este proyecto
AUTOCULL_PY = Path(r"C:\Renzo\Proyectos\Autocull\venv\Scripts\python.exe")  # el venv no se mueve
NTFY = AQUI / "ntfy_tema.txt"  # tema privado de ntfy (una linea). Fuera de git: la lista blanca no incluye .txt
PANEL = "https://rbfotos.studio/panel/revision/"
APUNTES = AQUI / "data" / "apuntes_renato.md"
BITACORA = SALIDA / "bitacora.md"
SISTEMA = ("Eres editor de fotografia deportiva. Miras imagenes con la herramienta Read y respondes "
           "solo con JSON valido, sin texto antes ni despues.")
VER = "ver_id"             # fotos a 1280 px CON su id escrito encima (ver achicar); "ver" eran las sin id
MODELO_FILTRO = "haiku"    # nota gruesa sobre hojas de contacto: barato
MODELO_CLAUDE = "sonnet"   # ponytail: sonnet cuida la cuota semanal; "opus" si el gusto no alcanza
VISTOS = SALIDA / "vistos.json"   # trabajos ya conocidos: lo que no esta aca es una fecha nueva
ESTABLE_HORAS = 2                  # una fecha se analiza recien cuando termino de copiarse
ELEGIR = 40  # 7/10/2026: Renzo pidio "hartas mas" que 20 por fecha para revisar.
MAX_A_CLAUDE = 80  # el juez lee todas juntas a 1280 px (~1.800 tokens c/u): 80 es ~145 mil, cerca del techo
TOP_RENATO = 20    # de las ~100 que elige Renato, las mejores para Instagram (segunda seccion del panel)
PX_RENATO = 1024   # sus ~100 a 1280 px no caben en una sola lectura; a 1024 son ~1.100 tokens c/u
# Autocull solo bota basura (3/10/2026). Con sus valores de siempre (cuotas fijas por percentil y 1 foto
# por rafaga) pasaba el 6-19% del carrete y dejaba afuera 9 de cada 10 fotos que eligio Renato:
# F1 13/9 -> 10 de 101, F2 26/9 -> 7 de 101 (48 cayeron como "duplicado de rafaga").
AUTOCULL_AJUSTES = {
    "sharpness_reject_percentile": 5.0,     # era 15: solo lo claramente movido o fuera de foco
    "blur_reject_percentile": 3.0,          # era 10
    "subject_snr_reject_percentile": 10.0,  # era 25
    "final_reject_percentile": 0.0,         # era 30: sin cuota final, el corte lo hacen Haiku y Sonnet
    "burst_keep": 3,                        # era 1: las 3 mejores de cada rafaga; Claude no repite jugada
}
RANKER = AQUI / "ranker_renato.json"  # ordena las candidatas (aprendido de Renato: p@30 0.37 vs 0.18)
MAX_A_FILTRO = 900                    # tope al filtro de Haiku. Simulado 3/10: 600 -> 900 suma ~12% de elegidas de Renato
MAX_NO_LEIDAS = 0.05                  # si mas del 5% no se pudo leer, el analisis no vale
POR_HOJA = 9               # 3x3
HOJAS_POR_LLAMADA = 6      # ~54 fotos por claude -p: reparte el costo fijo de cada llamada
PX_VER = 1280              # lo que ve el juez final


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def raiz_trabajo():
    """K: si el 8TB esta por dock (o mapeado); si no, el share de la Pi por LAN; si la notebook no esta
    en la red de la casa, por Tailscale (~4-5 MB/s: un carrete de JPEG tarda ~40 min en leerse, pero
    anda; 3/10/2026, Renzo fuera de casa con la notebook prendida). Recien despertada el wifi tarda en
    volver, por eso reintenta un rato."""
    for _ in range(36):
        for base in ("K:\\", r"\\192.168.1.62\Fotos", r"\\100.78.164.81\Fotos"):
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

def no_leidas(d):
    return sum(1 for f in d["fotos"] if (f.get("motivo") or "").startswith("Could not load"))


def autocull(trabajo, carpeta):
    """Las rutas JPEG candidatas: lo que Autocull no considero basura, las mejores primero segun el
    ranker aprendido de Renato, hasta MAX_A_FILTRO. Reusa el resultado si ya corrio."""
    salida = carpeta / "autocull.json"
    try:
        d = json.loads(salida.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # F3 3/10: una corrida cortada a la mitad dejo el archivo a medio escribir y los 3
        # reintentos fallaron en esta linea en vez de volver a correr Autocull
        d = {}
    if d.get("estado") != "listo":
        fotos = jpegs(trabajo)
        if not fotos:
            raise RuntimeError("el trabajo no tiene JPEG")
        log(f"Autocull sobre {len(fotos)} JPEG (esto tarda)")
        lista = carpeta / "autocull.lista.txt"
        lista.write_text("\n".join(fotos), encoding="utf-8")
        ajustes = dict(AUTOCULL_AJUSTES)
        if RANKER.exists():
            ajustes["ranker_path"] = str(RANKER)
        (carpeta / "autocull.ajustes.json").write_text(json.dumps(ajustes, indent=1), encoding="utf-8")
        with open(carpeta / "autocull.log", "w", encoding="utf-8", errors="replace") as fh:
            subprocess.run([str(AUTOCULL_PY), str(AQUI / "autocull_runner.py"), str(lista), str(salida),
                            str(carpeta / "autocull.ajustes.json")],
                           cwd=str(AUTOCULL_DIR), stdout=fh, stderr=subprocess.STDOUT, check=True)
        d = json.loads(salida.read_text(encoding="utf-8"))
        if d.get("estado") != "listo":
            raise RuntimeError(f"Autocull termino en estado {d.get('estado')}: {d.get('error')}")
        malas = no_leidas(d)
        if malas > MAX_NO_LEIDAS * len(d["fotos"]):
            # F2 26/9: un corte de red dejo 1.151 de 1.990 fotos sin leer y la seleccion se hizo
            # sobre menos de la mitad del carrete. Mejor no dar el analisis por bueno.
            salida.replace(carpeta / "autocull.fallido.json")
            raise RuntimeError(f"{malas} de {len(d['fotos'])} fotos no se pudieron leer (NAS inestable); "
                               "se reintenta en la proxima corrida")
    pasan = sorted((f for f in d["fotos"] if f["decision"] == "PASS"), key=lambda f: -(f.get("puntaje") or 0))
    log(f"Autocull: {len(pasan)} candidatas de {len(d['fotos'])} ({no_leidas(d)} sin leer); "
        f"al filtro van {min(len(pasan), MAX_A_FILTRO)}")
    return [f["ruta"] for f in pasan[:MAX_A_FILTRO]]


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


def achicar(rutas, carpeta, px=PX_VER):
    """C001.jpg... a 1280 px para los jueces, con el id escrito encima. Devuelve {id: ruta original}."""
    carpeta.mkdir(parents=True, exist_ok=True)
    mapa = {}
    for i, ruta in enumerate(rutas, 1):
        fid = f"C{i:03d}"
        destino = carpeta / f"{fid}.jpg"
        if not destino.exists():
            im = Image.open(ruta)
            im.draft("RGB", (px, px))  # decodifica el JPEG ya reducido: 42 MP pesan
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((px, px))
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


def claude(carpeta, ids, evento, elegir=ELEGIR):
    """Paso A: elige y ordena. Solo ids y por que; detallar() mira despues solo a esas."""
    prompt = f"""{CRITERIO}{apuntes()}
Trabajo: {evento}.
En esta carpeta hay {len(ids)} fotos candidatas: {', '.join(i + '.jpg' for i in ids)}.
Lee TODAS con la herramienta Read, varias en paralelo por turno para no gastar turnos.
{_ID_EN_IMAGEN}

Elige hasta {elegir} candidatas para publicar (menos si no hay {elegir} buenas de verdad). El
fotografo las revisa despues y se queda con algunas, asi que conviene variedad. Reglas:
- Nunca dos fotos de la misma jugada o del mismo momento: quedate con la mejor.
- Variedad: distintos jugadores/equipos y tipos de momento (accion, arquero, celebracion, historia).
- Ordenalas de la mas fuerte a la mas floja: las primeras 10 deben ser las que publicarias si
  solo pudieras publicar 10.
- motivo: una frase de por que esa foto, describiendo lo que SE VE en ella.
- apunte: 2 a 4 lineas para tu bitacora: que hizo fuerte (o flojo) este trabajo, que patron viste,
  que harias distinto la proxima vez. Queda anotado para las siguientes selecciones.

Responde SOLO este JSON:
{{"apunte": "...", "elegidas": [{{"id": "C001", "motivo": "..."}}]}}"""
    datos, uso = claude_json(prompt, carpeta, MODELO_CLAUDE)
    return [e for e in datos["elegidas"] if e.get("id") in ids], datos.get("apunte", ""), uso


def detallar(carpeta, elegidas, evento):
    """Paso B: mira SOLO las elegidas (pocas, sin confundirse): que se ve en cada una y su caption.

    La 'vista' es la verificacion: el motivo del paso A puede venir de otra foto (paso la primera
    noche, 6 de 10); esto se escribe mirando ESA imagen sola."""
    ids = [e["id"] for e in elegidas]
    prompt = f"""Trabajo: {evento}. Estas {len(ids)} fotos ya fueron elegidas para Instagram:
{', '.join(i + '.jpg' for i in ids)}. Lee cada una con Read. {_ID_EN_IMAGEN}

Para cada id:
- vista: una frase con lo que se ve en ESA imagen (sirve para verificar que no la confundiste).
- caption: una linea en español de Chile (tuteo chileno, nunca voseo), sin hashtags.

Responde SOLO este JSON:
{{"<id>": {{"vista": "...", "caption": "..."}}}}"""
    datos, uso = claude_json(prompt, carpeta, MODELO_CLAUDE)
    for e in elegidas:
        e.update(datos.get(e["id"], {}))
    return elegidas, uso


def anotar(evento, elegidas, apunte):
    """Bitacora legible de cada seleccion: que se eligio y por que."""
    lineas = [f"\n## {time.strftime('%Y-%m-%d %H:%M')} — {evento}\n", (apunte or "").strip(), ""]
    lineas += [f"{n}. `{e['archivo']}` — {e.get('vista') or e.get('motivo', '')}" for n, e in enumerate(elegidas, 1)]
    with open(BITACORA, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lineas) + "\n")


# ---------------------------------------------------------------- revision en /panel + aviso

def publicar(base, carpeta, evento, n):
    """Deja la seleccion en el NAS para /panel/revision (galeria.py en la Pi) y avisa por ntfy.

    *base* es <NAS>\\Fotos\\Galeria\\Trabajo; la Pi lee <NAS>\\_para_publicar\\<trabajo>\\seleccion.json
    y sirve los originales directo del disco: aca no se copia ninguna foto."""
    nas = Path(base).parents[2] / "_para_publicar" / evento
    nas.mkdir(parents=True, exist_ok=True)
    shutil.copy2(carpeta / "seleccion.json", nas / "seleccion.json")
    avisar(f"{n} fotos listas para revisar", evento, PANEL + urllib.parse.quote(evento))


def avisar(titulo, mensaje, link=None):
    """Notificacion al telefono por ntfy.sh. Sin ntfy_tema.txt no avisa (no es error)."""
    if not NTFY.exists():
        return
    datos = {"topic": NTFY.read_text(encoding="utf-8").strip(), "title": titulo, "message": mensaje,
             "tags": ["camera"]}
    if link:
        datos["click"] = link
    # JSON y no headers: el titulo y el trabajo llevan tildes, y los headers HTTP no aceptan UTF-8
    req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(datos).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:  # sin aviso igual queda la seleccion: no tumba nada
        log(f"ntfy fallo: {e}")


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
    uso = {"eleccion": uso, "detalle": uso_b}
    log(f"detalle de {len(elegidas)}; uso: {json.dumps(uso)[:300]}")
    for n, e in enumerate(elegidas, 1):
        e["original"] = mapa[e["id"]]
        e["archivo"] = f"{n:02d}_{Path(e['original']).name}"
        shutil.copy2(e["original"], carpeta / e["archivo"])  # completa, sin recomprimir; EXIF intacto
    (carpeta / "seleccion.json").write_text(json.dumps(
        {"trabajo": evento, "apunte": apunte, "elegidas": elegidas, "uso_claude": uso, "filtro_si": si,
         "autocull_pass": len(mapa)}, ensure_ascii=False, indent=1), encoding="utf-8")
    anotar(evento, elegidas, apunte)
    try:
        publicar(base, carpeta, evento, len(elegidas))
    except OSError as e:  # la seleccion ya quedo en la notebook; el panel se puede publicar despues
        log(f"no pude publicar en el NAS para /panel: {e}")
    log(f"listo: {carpeta}")


# ---------------------------------------------------------------- top de lo que eligio Renato

# Corre en la Pi: los JPEG de todas las listas del cliente que apreto "Termine mi seleccion".
CONSULTA_RENATO = r'''
import json, sqlite3
c = sqlite3.connect("/home/yenllo/galeria/galeria.db")
out = []
for gid, cid in c.execute("SELECT galeria_id, cliente_id FROM seleccion_lista").fetchall():
    out.append(sorted({r[0] for r in c.execute(
        "SELECT f.ruta FROM lista l JOIN lista_foto lf ON lf.lista_id = l.id JOIN foto f ON f.id = lf.foto_id "
        "WHERE l.cliente_id = ? AND f.galeria_id = ?", (cid, gid))}))
print(json.dumps(out))
'''
MAX_INTENTOS_RENATO = 3


def trabajo_de(foto):
    """El trabajo de una foto: la primera carpeta hacia arriba que tiene RAW/ adentro."""
    return next((p for p in foto.parents if (p / "RAW").is_dir()), None)


def renato(base):
    """Top de cada seleccion terminada de Renato que todavia no lo tenga. Sin la Pi, no hace nada."""
    try:
        r = subprocess.run(["ssh", "fotosnas", "python3", "-"], input=CONSULTA_RENATO, capture_output=True,
                           text=True, encoding="utf-8", timeout=60, check=True)
    except (OSError, subprocess.SubprocessError) as e:
        return log(f"top de Renato: no llegue a la Pi ({str(e)[:100]})")
    for rutas in json.loads(r.stdout):
        fotos = [base / Path(x.split("/Galeria/Trabajo/", 1)[1]) for x in rutas if "/Galeria/Trabajo/" in x]
        trabajo = trabajo_de(fotos[0]) if fotos else None
        if trabajo is None:
            continue
        carpeta = SALIDA / nombre_salida(base, trabajo)
        intentos = carpeta / "renato.intentos"
        n = int(intentos.read_text()) if intentos.exists() else 0
        if (carpeta / "renato.json").exists() or n >= MAX_INTENTOS_RENATO:
            continue
        try:
            procesar_renato(base, trabajo, [str(f) for f in fotos])
        except Exception as e:
            carpeta.mkdir(parents=True, exist_ok=True)
            intentos.write_text(str(n + 1))
            log(f"top de Renato {trabajo.name} fallo ({n + 1}/{MAX_INTENTOS_RENATO}): {e}")


def procesar_renato(base, trabajo, rutas):
    evento = nombre_salida(base, trabajo)
    carpeta = SALIDA / evento
    log(f"=== top de Renato: {evento} ({len(rutas)} fotos)")
    ver = carpeta / "ver_renato"
    mapa = achicar(rutas, ver, PX_RENATO)
    contexto = (f"{evento}. Estas {len(mapa)} fotos ya las eligio el cliente (Renato, organizador de la "
                f"liga) de todo el carrete: elige las mejores para el Instagram del fotografo")
    elegidas, apunte, uso = claude(ver, list(mapa), contexto, TOP_RENATO)
    elegidas, uso_b = detallar(ver, elegidas, evento)
    for n, e in enumerate(elegidas, 1):
        e["original"] = mapa[e["id"]]
        e["archivo"] = f"R{n:02d}_{Path(e['original']).name}"  # R: no choca con las de seleccion.json
    (carpeta / "renato.json").write_text(json.dumps(
        {"trabajo": evento, "apunte": apunte, "elegidas": elegidas, "de": len(mapa),
         "uso_claude": {"eleccion": uso, "detalle": uso_b}}, ensure_ascii=False, indent=1), encoding="utf-8")
    nas = Path(base).parents[2] / "_para_publicar" / evento
    nas.mkdir(parents=True, exist_ok=True)
    shutil.copy2(carpeta / "renato.json", nas / "renato.json")
    avisar(f"Top {len(elegidas)} de la seleccion de Renato", evento, PANEL + urllib.parse.quote(evento))
    log(f"top de Renato listo: {len(elegidas)} de {len(mapa)}")


def prueba():
    assert extraer_json('bla {"a": {"b": 1}} fin') == {"a": {"b": 1}}
    assert extraer_json('{"a": "linea1\nlinea2"}') == {"a": "linea1\nlinea2"}
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "F3" / "RAW").mkdir(parents=True)
        (Path(d) / "F3" / "jpeg").mkdir()
        assert trabajo_de(Path(d) / "F3" / "jpeg" / "a.JPG") == Path(d) / "F3"
        assert trabajo_de(Path(d) / "otro" / "a.JPG") is None
    print("ok")


def ultima_modificacion(trabajo):
    """mtime mas reciente entre las CARPETAS del trabajo: copiar un archivo cambia el de su carpeta,
    asi que no hace falta hacer stat a miles de fotos por la red. Compras\\ no cuenta: ahi caen los
    pedidos y entregas de clientes (7/10: la lista de Renato copiandose atraso la seleccion de la F3)."""
    ultima = 0
    for r, dirs, _ in os.walk(trabajo):
        dirs[:] = [d for d in dirs if d.lower() != "compras"]
        ultima = max(ultima, os.stat(r).st_mtime)
    return ultima


def fechas_nuevas(base):
    """Trabajos que no estan en vistos.json y ya terminaron de copiarse. -> (listos, vistos)."""
    todos = {str(t.relative_to(base)): t for t in trabajos(base)}
    if VISTOS.exists():
        vistos = set(json.loads(VISTOS.read_text(encoding="utf-8")))
    else:
        # Primera vez: el historial no se analiza. Se respeta la regla anterior (lo tocado en las
        # ultimas 2 semanas sin seleccion sigue pendiente) para no perder lo que ya esperaba.
        limite = time.time() - 14 * 86400
        vistos = {r for r, t in todos.items()
                  if t.stat().st_mtime < limite or (SALIDA / nombre_salida(base, t) / "seleccion.json").exists()}
        VISTOS.write_text(json.dumps(sorted(vistos), ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"vistos.json creado: {len(vistos)} trabajos conocidos, {len(todos) - len(vistos)} pendientes")
    listos = []
    for r, t in todos.items():
        if r in vistos:
            continue
        horas = (time.time() - ultima_modificacion(t)) / 3600
        if horas < ESTABLE_HORAS:
            log(f"{r}: cambio hace {horas:.1f} h, espero a que termine de copiarse")
        else:
            listos.append(t)
    return listos, vistos


def marcar_visto(base, trabajo, vistos):
    vistos.add(str(trabajo.relative_to(base)))
    VISTOS.write_text(json.dumps(sorted(vistos), ensure_ascii=False, indent=1), encoding="utf-8")


MAX_FALLOS = 3


def anotar_fallo(base, trabajo, vistos):
    """Cuenta fallos por trabajo. Al tercero lo da por visto (con chequeo cada hora, un trabajo que
    falla despues de llamar a Claude gastaria cuota cada hora para siempre). -> True si sigue intentando."""
    archivo = SALIDA / "fallos.json"
    fallos = json.loads(archivo.read_text(encoding="utf-8")) if archivo.exists() else {}
    rel = str(trabajo.relative_to(base))
    fallos[rel] = fallos.get(rel, 0) + 1
    archivo.write_text(json.dumps(fallos, ensure_ascii=False, indent=1), encoding="utf-8")
    if fallos[rel] >= MAX_FALLOS:
        marcar_visto(base, trabajo, vistos)
        log(f"{rel}: {MAX_FALLOS} fallos, lo dejo (para reintentar: sácalo de vistos.json y fallos.json)")
        return False
    return True


def candado():
    """Lock de archivo: si otra corrida esta trabajando, None. Se suelta solo si el proceso muere."""
    import msvcrt
    fh = open(SALIDA / ".corriendo", "a+")
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        fh.close()
        return None
    return fh


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
    espera = SALIDA / "esperar_hasta.txt"  # "2026-10-03 23:00": pausa la noche hasta esa hora
    if args == ["--noche"] and espera.exists():
        try:
            hasta = time.mktime(time.strptime(espera.read_text(encoding="utf-8").strip()[:16], "%Y-%m-%d %H:%M"))
        except ValueError:
            hasta = 0
        if time.time() < hasta:
            return
        espera.unlink()
    lock = candado()  # noqa: F841 (se mantiene abierto hasta que termina el proceso)
    if lock is None:
        return log("otra corrida sigue trabajando, salgo")
    cola = [l.strip() for l in COLA.read_text(encoding="utf-8").splitlines() if l.strip()] if COLA.exists() else []
    vistos = None
    if args == ["--noche"]:
        nuevos, vistos = fechas_nuevas(base)
        todo = nuevos + [base / c for c in cola]
    elif args == ["--renato"]:
        todo = []
    else:
        todo = [base / a for a in args]
    if args in (["--noche"], ["--renato"]):
        # antes que los carretes: son ~100 fotos y no deben esperar horas detras de uno de 3.000.
        # Sin seleccion terminada de Renato pendiente, es una consulta de un segundo a la Pi.
        renato(base)
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
            if vistos is not None:  # recien cuando termino bien: si fallo, la proxima corrida lo reintenta
                marcar_visto(base, trabajo, vistos)
            if rel in cola:  # sale de la cola recien cuando termino bien
                cola.remove(rel)
                COLA.write_text("".join(c + "\n" for c in cola), encoding="utf-8")
        except Exception as e:
            log(f"{trabajo} fallo: {e}")
            if vistos is not None and not anotar_fallo(base, trabajo, vistos):
                avisar("Una seleccion fallo 3 veces", f"{trabajo.name}: {str(e)[:200]}. Revisa noche.log")


if __name__ == "__main__":
    main()
