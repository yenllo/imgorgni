"""El reparto de estrellas del Autocull tiene que dar un embudo usable.

Con cortes absolutos sobre el puntaje daba 2 fotos de 5 estrellas y 308 de 3
(sobre F2, 2.158 fotos): el balde de arriba inservible y el del medio enorme.
Repartiendo por posicion entre las que pasan, cada nivel queda proporcional.

    python test_estrellas_autocull.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("CURADOR_SIN_MIGRAR", "1")

from web_curador import _repartir_estrellas


def main():
    # 100 que pasan con puntajes amontonados arriba (como sale de Autocull) y
    # 400 rechazadas, que es la proporcion real de un evento de Liga.
    fotos = [{"decision": "PASS", "puntaje": 50 + i * 0.45} for i in range(100)]
    fotos += [{"decision": "REJECT", "puntaje": 0} for _ in range(400)]
    _repartir_estrellas(fotos)

    cuenta = {}
    for f in fotos:
        cuenta[f["estrellas"]] = cuenta.get(f["estrellas"], 0) + 1

    assert cuenta[0] == 400, "las rechazadas tienen que quedar todas en 0 estrellas"
    assert cuenta[5] == 5, f"5 estrellas tiene que ser el top 5%, dio {cuenta.get(5)}"
    assert cuenta[4] == 15, f"4 estrellas es el siguiente 15%, dio {cuenta.get(4)}"
    assert cuenta[3] == 30, f"3 estrellas es el siguiente 30%, dio {cuenta.get(3)}"
    assert sum(cuenta[e] for e in (1, 2, 3, 4, 5)) == 100, "no se pierde ninguna que paso"

    # la mejor puntuada tiene que estar entre las de 5 estrellas
    mejor = max((f for f in fotos if f["decision"] == "PASS"), key=lambda f: f["puntaje"])
    assert mejor["estrellas"] == 5, "la de mayor puntaje tiene que ser de 5 estrellas"

    # sin ninguna que pase no puede explotar
    solo_malas = [{"decision": "REJECT", "puntaje": 0} for _ in range(3)]
    _repartir_estrellas(solo_malas)
    assert all(f["estrellas"] == 0 for f in solo_malas)

    print(f"OK: embudo {cuenta[5]}/{cuenta[4]}/{cuenta[3]}/{cuenta[2]}/{cuenta[1]} "
          f"estrellas sobre 100 que pasan, {cuenta[0]} descartadas")


if __name__ == "__main__":
    main()
