"""Encola en Lightroom la seleccion de Renato de cada galeria donde apreto "Termine mi seleccion"
(tabla seleccion_lista de la Pi). Favoritos -> "IMGorgni Basica"; EDICION COMPLETA -> "IMGorgni
Completa base" (ruido IA) + nivelado. SIN exportar: Renzo revisa foto por foto y exporta el.

Hace lo mismo que el boton "Lista solicitada" del panel (solicitar_compra de galeria.py): RAW a
<evento>/Compras/pedidos/<ID>-<lista> y entregados/<ID>-<lista>/RAW y /Export. Si el evento tiene copia local
(TrabajoFotografico\\*\\<evento>), arma lo mismo ahi copiando desde su RAW\\ y Lightroom trabaja local.
    python editar_renato.py            # solo encola lo nuevo
    python editar_renato.py --abrir    # y abre Lightroom (catalogo Automatico), que procesa la cola
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from seleccion_ig import raiz_trabajo

AQUI = Path(__file__).resolve().parent
LR = AQUI / "_lightroom"
COLA = LR / "cola.tsv"
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


def main():
    r = subprocess.run(["ssh", "fotosnas", "python3", "-"], input=SOLICITAR, capture_output=True,
                       text=True, encoding="utf-8", check=True)
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
        preset, nivelar = PRESETS[tipo]
        titulo = p["galeria"].replace("Seleccion ", "")
        cola += f"{jid}\t{ped}\t{preset}\t\t\t\t{titulo} - {p['codigo']} {tipo.capitalize()}\t{nivelar}\n"
        print(f"encolado {jid} ({tipo}): {len(p['raws'])} RAW en {ped}")
        nuevos += 1
    COLA.write_text(cola, encoding="utf-8")
    if not nuevos:
        print("nada nuevo")
    if "--abrir" in sys.argv:
        (LR / "procesar_al_abrir").touch()
        # Lightroom.exe tenia RUNASADMIN (Popen daba WinError 740; Renzo lo saco el 7/10).
        # ShellExecute anda en los dos casos.
        os.startfile(LR_EXE, arguments=f'"{CATALOGO}"')
        print("Lightroom abriendo: procesa la cola ~30 s despues de abrir")


if __name__ == "__main__":
    main()
