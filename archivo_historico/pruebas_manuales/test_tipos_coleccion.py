"""Los tipos de coleccion del curador tienen que tener carpeta en la exportacion.

Estaban desalineados: el <select> guardaba 'viaje_estudio' y la exportacion
esperaba 'vde', asi que esos eventos terminaban en el generico "Colecciones".

    python test_tipos_coleccion.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RAIZ = os.path.dirname(os.path.abspath(__file__))


def tipos_del_curador():
    """Los valores del <select> de tipo de coleccion en el template."""
    with open(os.path.join(RAIZ, "templates", "evento.html"), encoding="utf-8") as fh:
        html = fh.read()
    m = re.search(r"for t in \[([^\]]+)\]", html)
    assert m, "no encontre la lista de coleccion_tipo en evento.html"
    return set(re.findall(r"'([^']+)'", m.group(1)))


def tipos_de_la_exportacion():
    src = open(os.path.join(RAIZ, "4_exportar.py"), encoding="utf-8").read()
    m = re.search(r"TIPOS_COLECCION = \{(.*?)\}", src, re.S)
    assert m, "no encontre TIPOS_COLECCION en 4_exportar.py"
    return set(re.findall(r'"([^"]+)":', m.group(1)))


def main():
    curador, export = tipos_del_curador(), tipos_de_la_exportacion()
    faltan = curador - export
    assert not faltan, (
        f"el curador guarda {sorted(faltan)} pero la exportacion no los conoce: "
        "esos eventos caen al generico 'Colecciones'")
    sobran = export - curador
    assert not sobran, (
        f"la exportacion espera {sorted(sobran)}, que el curador ya no guarda")
    print(f"OK: los {len(curador)} tipos de coleccion coinciden entre curador y exportacion")


if __name__ == "__main__":
    main()
