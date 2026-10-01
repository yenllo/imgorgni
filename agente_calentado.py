#!/usr/bin/env python3
"""Agente de la notebook: toma los «Calentar ahora» del panel y los hace aca.

La Pi nunca se conecta a la notebook. Este agente le pregunta cada 30 s, por
el mismo SSH de siempre (LAN o Tailscale):

  1. si el disco se ve (Y: en casa o Z: por Tailscale), deja un latido en cache/notebook-vivo. Sin
     latido reciente la Pi calienta ella misma, asi que notebook apagada o
     sin disco nunca deja un pedido colgado.
  2. si hay cache/pedido-<slug>, lo renombra a trabajando-<slug> (asi no lo
     toma dos veces), baja galeria.py al dia y corre calentar_local.py.
  3. si el contador no cierra —tipico: cambiaron la marca mientras corria—
     reintenta una vez; si vuelve a fallar, suelta el pedido (no a la Pi).

Arranca solo al iniciar sesion (tarea programada "rbfotos agente calentado").

    pythonw agente_calentado.py            # lo que corre la tarea
    python  agente_calentado.py --una-vez  # una pasada, para probar
"""
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
from calentar_local import raiz_local   # Y: en casa, Z: por Tailscale

PI = "fotosnas"          # ~/.ssh/config lo apunta a la IP de Tailscale: anda en casa y afuera
CACHE = "/home/yenllo/galeria/cache"
CADA = 30
LOG = AQUI / "agente_calentado.log"
SLUG = re.compile(r"^[A-Za-z0-9_-]{6,40}$")
# sin esto cada ssh abre y cierra una ventana negra cada 30 s
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PYTHON = str(Path(sys.executable).with_name("python.exe"))   # no pythonw: queremos su salida


def log(msg):
    if LOG.exists() and LOG.stat().st_size > 2_000_000:
        LOG.write_text("", encoding="utf-8")
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write("%s  %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))


def ssh(cmd, timeout=40):
    return subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", PI, cmd],
                          capture_output=True, text=True, timeout=timeout,
                          creationflags=SIN_VENTANA)


def latir():
    return ssh("touch %s/notebook-vivo" % CACHE).returncode == 0


def tomar_pedidos():
    """Renombra pedido-* a trabajando-* en la Pi y devuelve los slugs."""
    r = ssh('cd %s && for f in pedido-*; do [ -e "$f" ] || continue; '
            's="${f#pedido-}"; mv "$f" "trabajando-$s" && echo "$s"; done' % CACHE)
    return [x for x in r.stdout.split() if SLUG.match(x)]


def calentar(slug):
    """0 = completo, otro = fallo. Mientras corre, un hilo mantiene el latido."""
    parar = threading.Event()

    def latido():
        while not parar.wait(CADA):
            try:
                latir()
            except Exception:
                pass

    hilo = threading.Thread(target=latido, daemon=True)
    hilo.start()
    try:
        subprocess.run([PYTHON, str(AQUI / "galeria_pi.py"), "traer"], cwd=AQUI,
                       capture_output=True, timeout=300, creationflags=SIN_VENTANA)
        r = subprocess.run([PYTHON, "-u", str(AQUI / "calentar_local.py"), "--", slug], cwd=AQUI,
                           # el "--" es obligatorio: hay slugs que empiezan con "-"
                           # y argparse los toma por flag (18/9/2026, -g1aw19AbLe5rzla)
                           capture_output=True, text=True, timeout=4 * 3600,
                           creationflags=SIN_VENTANA)
        for linea in r.stdout.replace("\r", "\n").splitlines():
            if "generado" in linea or "subido" in linea or "la Pi dice" in linea:
                log("  %s: %s" % (slug, linea.strip()))
        if r.returncode:
            log("  %s: salio con codigo %d %s" % (slug, r.returncode, r.stderr[-300:].strip()))
        return r.returncode
    finally:
        parar.set()


def atender(slug):
    log("pedido %s" % slug)
    for intento in (1, 2):
        if calentar(slug) == 0:
            log("listo %s (intento %d)" % (slug, intento))
            ssh("rm -f %s/trabajando-%s" % (CACHE, slug))
            return
        log("incompleto %s en el intento %d" % (slug, intento))
    # dos veces no cerro: se suelta el pedido y listo. Antes se lo devolvia a
    # la Pi, y con calor eso la reiniciaba (13/9/2026): mejor volver a apretar
    # "Calentar ahora" que tumbar el servidor.
    ssh("rm -f %s/trabajando-%s" % (CACHE, slug))
    log("abandonado %s: no se devuelve a la Pi" % slug)


def pasada():
    if raiz_local() is None:
        return                    # sin disco no hay latido: la Pi se arregla sola
    if not latir():
        return
    for slug in tomar_pedidos():
        atender(slug)


def main():
    una = "--una-vez" in sys.argv
    log("agente iniciado%s" % (" (una vez)" if una else ""))
    while True:
        try:
            pasada()
        except Exception as e:     # red caida, Pi reiniciando: se reintenta solo
            log("error: %r" % e)
        if una:
            return
        time.sleep(CADA)


if __name__ == "__main__":
    assert SLUG.match("Hgwer-N-GAUHzz04") and not SLUG.match("a;rm -rf /")
    assert SLUG.match("-g1aw19AbLe5rzla")   # slug con guion adelante: por eso el "--"
    main()
