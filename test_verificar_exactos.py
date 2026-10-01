"""python test_verificar_exactos.py"""
from verificar_exactos import DISTINTOS, FALTA, IGUALES, clasificar


def main():
    pares = [
        (1, "a.jpg", "b.jpg"),   # mismo contenido
        (2, "c.jpg", "d.jpg"),   # distinto: se daño uno despues de indexar
        (3, "e.jpg", "f.jpg"),   # uno no se pudo leer
        (4, "g.jpg", "h.jpg"),   # ninguno se pudo leer
    ]
    hashes = {"a.jpg": "aa", "b.jpg": "aa",
              "c.jpg": "cc", "d.jpg": "dd",
              "e.jpg": "ee", "f.jpg": None,
              "g.jpg": None, "h.jpg": None}
    r = clasificar(pares, hashes)
    assert [p[0] for p in r[IGUALES]] == [1], r[IGUALES]
    assert [p[0] for p in r[DISTINTOS]] == [2], r[DISTINTOS]
    assert sorted(p[0] for p in r[FALTA]) == [3, 4], r[FALTA]
    assert len(r[IGUALES]) + len(r[DISTINTOS]) + len(r[FALTA]) == len(pares), \
        "cada par tiene que caer en exactamente una categoria"

    # un archivo ilegible nunca puede contarse como 'sigue identico'
    assert not any(p[0] in (3, 4) for p in r[IGUALES])
    print("OK: separa identicos, cambiados e ilegibles sin colar ninguno")


if __name__ == "__main__":
    main()
