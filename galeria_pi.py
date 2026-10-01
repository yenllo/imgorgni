#!/usr/bin/env python3
"""Puente entre este proyecto y la galeria que corre en la Raspberry Pi.

`galeria.py` NO vive aca: vive en la Pi, en /home/yenllo/galeria/galeria.py, y
ahi es donde esta corriendo el sitio publico.

    python galeria_pi.py traer      # baja la copia viva de la Pi
    python galeria_pi.py probar     # self-check EN LA PI, sobre un temporal
    python galeria_pi.py subir      # prueba, respalda, sube, reinicia, verifica
                                    # y si algo falla VUELVE ATRAS solo

Por que `probar` no corre local: el self-check compara rutas con "/" y en
Windows Path las arma con "\\", asi que falla por el separador y no por el
codigo. Ademas probar donde de verdad va a correr es lo unico que vale. Se
sube a /tmp de la Pi, se corre ahi, y el archivo que sirve no se toca hasta
que pasa.

El sitio es publico: una portada rota se ve. Por eso `subir` no publica nada
que no pase el self-check, y si despues de reiniciar la Pi no contesta 200,
restaura el respaldo y reinicia de nuevo.
"""
import subprocess
import sys
import time
from pathlib import Path

PI = "fotosnas"
REMOTO = "/home/yenllo/galeria/galeria.py"
PRUEBA = "/tmp/galeria_a_probar.py"
LOCAL = Path(__file__).parent / "pi_galeria" / "galeria.py"


def ssh(cmd, **kw):
    return subprocess.run(["ssh", PI, cmd], text=True, capture_output=True, **kw)


def _empujar(destino):
    with open(LOCAL, "rb") as fh:
        return subprocess.run(["ssh", PI, "cat > " + destino], stdin=fh).returncode


def traer():
    LOCAL.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCAL, "wb") as fh:
        subprocess.run(["ssh", PI, "cat " + REMOTO], stdout=fh)
    if not LOCAL.exists() or LOCAL.stat().st_size < 10000:
        sys.exit("no pude traerlo")
    print("bajado: %s  (%d lineas)"
          % (LOCAL, LOCAL.read_text(encoding="utf-8").count("\n")))


def probar():
    """Sube una copia a /tmp de la Pi y corre ahi el self-check del archivo."""
    if not LOCAL.exists():
        sys.exit("no hay copia local; corre primero:  python galeria_pi.py traer")
    if _empujar(PRUEBA):
        sys.exit("no pude subir la copia de prueba")
    r = ssh("cd /home/yenllo/galeria && timeout 300 python3 -u %s test" % PRUEBA)
    salida = (r.stdout + r.stderr).strip()
    print(salida[-3000:] if salida else "(sin salida)")
    return r.returncode == 0


def subir():
    if not probar():
        sys.exit("\nEL SELF-CHECK FALLA. No subo nada.")
    print("self-check OK, publicando...")

    resp = "%s.bak-agy-%s" % (REMOTO, time.strftime("%Y%m%d-%H%M%S"))
    if ssh("cp %s %s" % (REMOTO, resp)).returncode:
        sys.exit("no pude respaldar en la Pi")
    print("  respaldo:", resp)

    if ssh("cp %s %s" % (PRUEBA, REMOTO)).returncode:
        sys.exit("no pude poner el archivo en su lugar")

    # el servicio corre como yenllo; SIGKILL cuenta como falla y systemd lo
    # levanta solo (con SIGTERM lo toma como salida limpia y no reinicia)
    ssh('pkill -9 -f "[g]aleria.py servir"')
    for _ in range(25):
        time.sleep(1)
        if ssh("curl -s -o /dev/null -w '%{http_code}' "
               "http://127.0.0.1:8000/").stdout.strip() == "200":
            print("  la Pi responde 200. Publicado.")
            return
    print("  LA PI NO RESPONDE. Vuelvo atras.")
    ssh("cp %s %s" % (resp, REMOTO))
    ssh('pkill -9 -f "[g]aleria.py servir"')
    time.sleep(8)
    sys.exit("  restaurado el respaldo; la Pi contesta %s"
             % ssh("curl -s -o /dev/null -w '%{http_code}' "
                   "http://127.0.0.1:8000/").stdout.strip())


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "traer":
        traer()
    elif cmd == "probar":
        sys.exit(0 if probar() else 1)
    elif cmd == "subir":
        subir()
    else:
        sys.exit(__doc__)
