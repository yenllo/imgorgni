"""Encola en Lightroom la seleccion de Renato de cada galeria donde apreto "Termine mi seleccion"
(tabla seleccion_lista de la Pi). Favoritos -> "IMGorgni Basica"; EDICION COMPLETA -> "IMGorgni
Completa base" (ruido IA) + nivelado. SIN exportar: Renzo revisa foto por foto y exporta el.

Hace lo mismo que el boton "Lista solicitada" del panel (solicitar_compra de galeria.py): RAW a
<evento>/Compras/pedidos/<ID>-<lista> y entregados/<ID>-<lista>/RAW y /Export. Si el evento tiene copia local
(TrabajoFotografico\\*\\<evento>), arma lo mismo ahi copiando desde su RAW\\ y Lightroom trabaja local.
    python editar_renato.py            # solo encola lo nuevo
    python editar_renato.py --abrir    # y si hay pendientes abre Lightroom (catalogo Automatico), espera
                                       # a que el plugin termine y avisa por ntfy como salio
La tarea "IMGorgni editar Renato" (registrar_tarea_ig.ps1) lo corre con --abrir a las 3:00.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from seleccion_ig import avisar, raiz_trabajo

AQUI = Path(__file__).resolve().parent
LR = AQUI / "_lightroom"
COLA = LR / "cola.tsv"
HECHOS = LR / "hechos.tsv"
FIN = LR / "fin.txt"              # el plugin lo reescribe al terminar la cola
ESPERA_MIN = 90                   # 100 RAW con presets y nivelado tardan minutos; mas que esto, algo fallo
LOCAL = AQUI / "TrabajoFotografico"
CATALOGO = r"C:\Fotos\otro\catalogos\Automatico\Automatico.lrcat"
LR_EXE = r"C:\Program Files\Adobe\Adobe Lightroom Classic\Lightroom.exe"
PRESETS = {"basicas": ("IMGorgni Basica", ""), "completas": ("IMGorgni Completa base", "si")}

# Corre en la Pi con el codigo de la galeria: mismo ID de pedido y misma copia que el boton.
SOLICITAR = r'''
import json, sys
sys.path.insert(0, "/home/yenllo/galeria")
import galeria as G
out = []
with G.app.test_request_context():
    cx = G.db()
    for s in cx.execute("SELECT s.galeria_id, s.cliente_id, g.slug, g.nombre FROM seleccion_lista s "
                        "JOIN galeria g ON g.id = s.galeria_id").fetchall():
        for l in cx.execute("SELECT id, nombre FROM lista WHERE cliente_id = ?", (s["cliente_id"],)).fetchall():
            _, _, fotos = G._lista_de(s["slug"], l["id"])
            if not fotos:
                continue
            cod, carpeta = G.codigo_lista(l["id"]), G.carpeta_pedido(l["id"], l["nombre"])
            G.solicitar_compra(fotos, carpeta)
            est = G.estado_compra(fotos, carpeta)
            if est["estado"] != "pedida":       # entregada o sin RAW: nada que editar
                continue
            out.append({"galeria": s["nombre"], "lista": l["nombre"], "codigo": cod,
                        "pedido": str(est["ped"]), "carpeta": est["ped"].name, "raws": [f["raw"] for f in fotos if f["raw"]]})
print(json.dumps(out))
'''


def evento_local(pedido_pi):
    """.../<evento>/Compras/pedidos/<ID>-<lista> en la Pi -> carpeta local del evento, o None."""
    evento = Path(pedido_pi).parents[2].name
    return next((p for p in LOCAL.glob(f"*/{evento}") if (p / "RAW").is_dir()), None)


def pedido_local(p):
    """Arma Compras/pedidos/<carpeta> y entregados/<carpeta>/RAW,Export en la copia local. -> carpeta o None."""
    ev = evento_local(p["pedido"])
    if ev is None:
        return None
    ped = ev / "Compras" / "pedidos" / p["carpeta"]
    ped.mkdir(parents=True, exist_ok=True)
    for sub in ("RAW", "Export"):
        (ev / "Compras" / "entregados" / p["carpeta"] / sub).mkdir(parents=True, exist_ok=True)
    for raw in p["raws"]:
        nombre = raw.rsplit("/", 1)[1]
        origen, destino = ev / "RAW" / nombre, ped / nombre
        if origen.exists() and not destino.exists():
            shutil.copyfile(origen, destino.with_name(nombre + ".parcial"))
            os.replace(destino.with_name(nombre + ".parcial"), destino)
    return ped


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def color_modelo(jid, carpeta):
    """Temperatura, matiz y exposicion del modelo de color de Renzo (color_modelo.py) para cada RAW de la
    tanda -> _lightroom/color_<jid>.tsv, que el plugin aplica encima del preset. Solo si el modelo paso
    la prueba (existe _color/activo); si algo falla, la tanda sigue con el preset solo."""
    if not (AQUI / "_color" / "activo").exists():
        return
    try:
        from color_modelo import predecir
        rutas = sorted(p for p in Path(carpeta).iterdir() if p.suffix.lower() == ".arw")
        pred = predecir(rutas)
        (LR / f"color_{jid}.tsv").write_text("".join(
            f"{r}\t{v['Temperature']}\t{v['Tint']}\t{v['Exposure2012']}\n" for r, v in pred.items()),
            encoding="utf-8")
        log(f"color del modelo para {len(pred)} fotos de {jid}")
    except Exception as e:
        log(f"modelo de color fallo en {jid}, queda el preset solo: {e}")


def pendientes():
    """Trabajos de la cola que el plugin todavia no termino bien (hechos.tsv con "ok")."""
    ok = set()
    if HECHOS.exists():
        for l in HECHOS.read_text(encoding="utf-8").splitlines():
            c = l.split("\t")
            if len(c) > 1 and c[1] == "ok":
                ok.add(c[0])
    ids = [l.split("\t")[0] for l in COLA.read_text(encoding="utf-8").splitlines()
           if l.strip() and not l.startswith("#")] if COLA.exists() else []
    return [i for i in ids if i not in ok]


def lightroom_abierto():
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Lightroom.exe", "/NH"], capture_output=True, text=True)
    return "lightroom.exe" in r.stdout.lower()


def abrir_y_esperar(pend, espera_min=ESPERA_MIN):
    """Deja la bandera, abre Lightroom si no estaba y espera fin.txt. El plugin (Inicio.lua) solo procesa
    con el catalogo Automatico abierto: si Renzo tenia abierto el suyo, no toca nada y se avisa."""
    antes = FIN.stat().st_mtime if FIN.exists() else 0
    import ctypes   # que Windows no suspenda la notebook mientras Lightroom trabaja (de noche nadie la toca)
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    (LR / "procesar_al_abrir").touch()
    if lightroom_abierto():
        log("Lightroom ya estaba abierto: no lanzo otro; procesa solo si tiene el catalogo Automatico")
    else:
        # Lightroom.exe tenia RUNASADMIN (Popen daba WinError 740; Renzo lo saco el 7/10).
        # ShellExecute anda en los dos casos.
        os.startfile(LR_EXE, arguments=f'"{CATALOGO}"')
        log("Lightroom abriendo con el catalogo Automatico")
    for _ in range(espera_min):
        time.sleep(60)
        if FIN.exists() and FIN.stat().st_mtime > antes:
            break
    else:
        log(f"Lightroom no proceso la cola en {espera_min} min")
        avisar("Lightroom no edito lo de Renato",
               f"{len(pend)} trabajo(s) siguen pendientes. Revisa _lightroom/plugin.log")
        return
    quedan = pendientes()
    hechos = [i for i in pend if i not in quedan]
    log(f"Lightroom termino: {len(hechos)} ok, {len(quedan)} pendientes")
    if quedan:
        avisar("Edicion de Renato con errores", f"Listos: {len(hechos)}. Con error: {', '.join(quedan)}. "
               "Detalle en _lightroom/plugin.log")
    else:
        avisar("Edicion de Renato lista", f"{len(hechos)} tanda(s) reveladas en Lightroom (catalogo "
               "Automatico, sin exportar). Estan en sus colecciones.")


def main():
    r = subprocess.run(["ssh", "fotosnas", "python3", "-"], input=SOLICITAR, capture_output=True,
                       text=True, encoding="utf-8")
    if r.returncode:
        # 11-12/10/2026: el 8TB quedo sin montar (NTFS sucio) y esto se caia con un traceback en
        # silencio dos noches seguidas. Ahora avisa: sin la Pi no hay seleccion que editar.
        detalle = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or ["sin detalle"]
        log(f"no pude pedir las listas a la Pi (ssh {r.returncode}): {detalle[0]}")
        avisar("Edicion de Renato: no llegue a la Pi",
               f"ssh salio con {r.returncode}: {detalle[0][:150]}. ¿Esta montado el 8TB?")
        return
    cola = COLA.read_text(encoding="utf-8") if COLA.exists() else "# id\torigen\tpreset_revelado\tpreset_exportacion\tdestino\tmarca_de_agua\tcoleccion\tnivelar\n"
    nuevos = 0
    for p in json.loads(r.stdout):
        jid = f"renato-{p['codigo']}"
        if f"\n{jid}\t" in "\n" + cola:
            continue
        tipo = "completas" if "completa" in p["lista"].lower() else "basicas"
        ped = pedido_local(p)
        if ped is None:  # sin copia local: Lightroom lee el pedido del NAS
            ped = raiz_trabajo() / Path(p["pedido"].split("/Galeria/Trabajo/", 1)[1])
        color_modelo(jid, ped)
        preset, nivelar = PRESETS[tipo]
        titulo = p["galeria"].replace("Seleccion ", "")
        cola += f"{jid}\t{ped}\t{preset}\t\t\t\t{titulo} - {p['codigo']} {tipo.capitalize()}\t{nivelar}\n"
        log(f"encolado {jid} ({tipo}): {len(p['raws'])} RAW en {ped}")
        nuevos += 1
    COLA.write_text(cola, encoding="utf-8")
    if not nuevos:
        log("nada nuevo de Renato")
    pend = pendientes()
    if "--abrir" in sys.argv:
        if pend:
            log(f"pendientes: {', '.join(pend)}")
            abrir_y_esperar(pend)
        else:
            log("nada pendiente en la cola: no abro Lightroom")


if __name__ == "__main__":
    main()
