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
  4. si hay cache/caras-pedido-<slug> y es de noche con la notebook en casa,
     corre caras_galeria.py: el analisis facial. Espera lo que haga falta: la
     Pi nunca analiza, asi que no hay a quien devolverselo.

Arranca solo al iniciar sesion (tarea programada "rbfotos agente calentado").

    pythonw agente_calentado.py            # lo que corre la tarea
    python  agente_calentado.py --una-vez  # una pasada, para probar
"""
import re
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
from calentar_local import raiz_local   # Y: en casa, Z: por Tailscale

from calentar_local import host_pi   # fotosnas-lan en casa, fotosnas afuera
CACHE = "/home/yenllo/galeria/cache"
CADA = 30
LOG = AQUI / "agente_calentado.log"
SLUG = re.compile(r"^[A-Za-z0-9_-]{6,40}$")
# sin esto cada ssh abre y cierra una ventana negra cada 30 s
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PYTHON = str(Path(sys.executable).with_name("python.exe"))   # no pythonw: queremos su salida
# el analisis facial exprime la CPU y sube el indice por LAN: solo de noche y en el depto.
# Puede terminar pasadas las 8 si partio antes: cortarlo botaria lo avanzado.
NOCHE_DESDE, NOCHE_HASTA = 22, 8
SSID_CASA = {"renzo", "renzo_5g"}


def log(msg):
    if LOG.exists() and LOG.stat().st_size > 2_000_000:
        LOG.write_text("", encoding="utf-8")
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write("%s  %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))


def ssh(cmd, timeout=40):
    return subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host_pi(), cmd],
                          capture_output=True, text=True, timeout=timeout,
                          creationflags=SIN_VENTANA)


def latir():
    return ssh("touch %s/notebook-vivo" % CACHE).returncode == 0


def tomar_pedidos():
    """Renombra pedido-* a trabajando-* en la Pi y devuelve los slugs."""
    r = ssh('cd %s && for f in pedido-*; do [ -e "$f" ] || continue; '
            's="${f#pedido-}"; mv "$f" "trabajando-$s" && echo "$s"; done' % CACHE)
    return [x for x in r.stdout.split() if SLUG.match(x)]


@contextmanager
def latiendo():
    """Mientras dura el bloque, un hilo mantiene el latido."""
    parar = threading.Event()

    def latido():
        while not parar.wait(CADA):
            try:
                latir()
            except Exception:
                pass

    threading.Thread(target=latido, daemon=True).start()
    try:
        yield
    finally:
        parar.set()


def calentar(slug):
    """0 = completo, otro = fallo."""
    with latiendo():
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


def es_noche(ahora=None):
    h = (ahora or datetime.now()).hour
    return h >= NOCHE_DESDE or h < NOCHE_HASTA


def ssids(salida):
    """Las redes de `netsh wlan show interfaces`; el BSSID no calza por el ^."""
    return re.findall(r"^\s*SSID\s*:\s*(.+?)\s*$", salida, re.M)


def en_casa():
    r = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True,
                       errors="replace", timeout=15, creationflags=SIN_VENTANA)
    return any(s.lower() in SSID_CASA for s in ssids(r.stdout))


def tomar_caras():
    """Uno por pasada, para volver a mirar la hora antes del siguiente. Va primero
    caras-trabajando-*: el agente hace una cosa a la vez, asi que si hay uno es de
    una notebook que se apago a mitad, y se retoma."""
    r = ssh('cd %s && for f in caras-trabajando-* caras-pedido-*; do [ -e "$f" ] || continue; '
            's="${f#caras-*-}"; [ "$f" = "caras-trabajando-$s" ] || mv "$f" "caras-trabajando-$s"; '
            'echo "$s"; break; done' % CACHE)
    return [x for x in r.stdout.split() if SLUG.match(x)]


def analizar(slug):
    log("analisis facial %s" % slug)
    with latiendo():
        try:
            r = subprocess.run([PYTHON, "-u", str(AQUI / "caras_galeria.py"), "--", slug], cwd=AQUI,
                               capture_output=True, text=True, errors="replace", timeout=12 * 3600,
                               creationflags=SIN_VENTANA)
            codigo, error = r.returncode, r.stderr.strip()
        except subprocess.TimeoutExpired:
            codigo, error = 1, "paso de 12 horas"
    if codigo:
        # el panel muestra la ultima linea: es la que dice que paso
        error = (error.splitlines() or ["fallo sin mensaje"])[-1][:300]
        log("  %s: fallo: %s" % (slug, error))
        subprocess.run(["ssh", "-o", "BatchMode=yes", host_pi(), "cat > %s/caras-error-%s" % (CACHE, slug)],
                       input=error, text=True, encoding="utf-8", capture_output=True, timeout=40,
                       creationflags=SIN_VENTANA)
    else:
        ssh("rm -f %s/caras-error-%s" % (CACHE, slug))
        log("listo analisis %s" % slug)
    ssh("rm -f %s/caras-trabajando-%s" % (CACHE, slug))


def pasada():
    # sin disco no hay latido: la Pi se arregla sola
    if raiz_local() is not None and latir():
        for slug in tomar_pedidos():
            atender(slug)
    # no usa Y: lee TrabajoFotografico, por eso no depende del disco de arriba
    if es_noche() and en_casa():
        for slug in tomar_caras():
            analizar(slug)


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
    assert es_noche(datetime(2026, 11, 4, 22)) and es_noche(datetime(2026, 11, 4, 7, 59))
    assert not es_noche(datetime(2026, 11, 4, 8)) and not es_noche(datetime(2026, 11, 4, 21, 59))
    assert ssids("  BSSID   : aa:bb\n    SSID     : Renzo_5G\n") == ["Renzo_5G"]
    main()
