"""python test_verificar_copia_mejor.py"""
import sqlite3

from verificar_copia_mejor import verificar

ESQUEMA = """CREATE TABLE files (id INTEGER PRIMARY KEY, file_path TEXT, borrado INTEGER DEFAULT 0);
CREATE TABLE duplicados (id INTEGER PRIMARY KEY, decision TEXT,
    id_conservar INT, id_descartar INT);"""


def main():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(ESQUEMA)
    c.executemany("INSERT INTO files VALUES (?,?,?)", [
        (1, r"K:\a\bueno.jpg", 0),      # ok: vivo en disco
        (2, r"K:\a\basurero.jpg", 1),   # conservar pero esta en el basurero
        (3, r"K:\a\encadenado.jpg", 0), # conservar en un par, descartado en otro
        (4, r"K:\a\otro.jpg", 0),
        (5, r"K:\a\noexiste.jpg", 0),   # conservar pero no esta en disco
        (6, r"K:\_duplicados_cuarentena\x\y.jpg", 0),  # conservar ya en cuarentena
        (7, r"K:\a\pedido.jpg", 0),     # se quiere borrar, pero otro par lo conserva
        (8, r"K:\a\depende.jpg", 0),    # el que depende de 7
        (9, r"K:\a\limpio.jpg", 0),     # conservar sano, sin cadena
    ])
    c.executemany("INSERT INTO duplicados (id, decision, id_conservar, id_descartar) VALUES (?,?,?,?)", [
        (1, "descartar", 1, 99),   # bien
        (2, "movido", 2, 98),      # mal: basurero
        (3, "descartar", 3, 4),    # 3 se conserva aca...
        (4, "movido", 4, 3),       # ...pero 3 se descarta aca: cadena rota
        (5, "descartar", 5, 97),   # mal: no existe en disco
        (6, "movido", 6, 96),      # mal: conservar en cuarentena
        (7, "pendiente", 1, 95),   # pendiente: no se chequea
        (8, "descartar", 9, 7),    # mal: borra el 7, que el par 9 da por bueno
        (9, "pendiente", 7, 8),    # todavia sin resolver, pero depende del 7 vivo
    ])
    c.commit()

    existe = lambda p: p != r"K:\a\noexiste.jpg"
    problemas = verificar(c, existe=existe)
    motivos = {p["id"]: p["motivo"] for p in problemas}

    assert 1 not in motivos, "el par sano no deberia dar problema"
    assert 7 not in motivos, "un pendiente no se chequea"
    assert "basurero" in motivos[2]
    assert "descartado de otro par" in motivos[3] and "descartado de otro par" in motivos[4], \
        "la cadena rota tiene que marcar ambos lados"
    assert "no esta en el disco" in motivos[5]
    assert "cuarentena" in motivos[6]
    assert "copia buena de otro par" in motivos[8], \
        "borrar la copia que otro par sin resolver da por buena tiene que avisar"
    assert 9 not in motivos, "el par pendiente en si no se reporta, solo el que borra"
    assert len(problemas) == 6

    print("OK: detecta basurero, cadena rota, archivo faltante, conservar en "
          "cuarentena y borrar la copia buena de un par sin resolver")


if __name__ == "__main__":
    main()
