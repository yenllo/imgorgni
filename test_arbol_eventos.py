"""Chequeo del arbol de eventos: quien puede ser padre de quien.

Colgar un evento de su propio sub-evento arma un ciclo, y el arbol se recorre
con un CTE recursivo. Sin este corte, esa consulta no termina.

    python test_arbol_eventos.py
"""
import sqlite3

from web_curador import _fechas_desde_hijos, _subir_fechas, descendientes

ESQUEMA = """CREATE TABLE events (id INTEGER PRIMARY KEY, parent_id INTEGER,
                                  start_time TEXT, end_time TEXT);"""


def base(filas):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(ESQUEMA)
    c.executemany("INSERT INTO events (id, parent_id) VALUES (?,?)", filas)
    c.commit()
    return c


def fechas(c, eid):
    r = c.execute("SELECT start_time a, end_time b FROM events WHERE id=?", (eid,)).fetchone()
    return (r["a"], r["b"])


def test_fechas_suben_hasta_la_raiz():
    """1 ── 2 ── 3(hoja): cambiar la hoja tiene que mover al abuelo tambien."""
    c = base([(1, None), (2, 1), (3, 2)])
    c.execute("UPDATE events SET start_time='2024-01-01', end_time='2024-01-05' WHERE id=3")
    _subir_fechas(c, 3)
    assert fechas(c, 2) == ("2024-01-01", "2024-01-05"), fechas(c, 2)
    assert fechas(c, 1) == ("2024-01-01", "2024-01-05"), "el abuelo quedaba con fechas viejas"

    # una segunda hoja mas nueva ensancha el rango de los dos ancestros
    c.execute("INSERT INTO events VALUES (4, 2, '2024-03-01', '2024-03-02')")
    _subir_fechas(c, 4)
    assert fechas(c, 1) == ("2024-01-01", "2024-03-02"), fechas(c, 1)
    print("OK: el rango de fechas sube hasta la raiz del arbol")


def test_ciclo_no_cuelga_las_fechas():
    c = base([(1, 2), (2, 1)])
    c.execute("UPDATE events SET start_time='2024-01-01' WHERE id=1")
    _subir_fechas(c, 1)   # sin el set de vistos, esto gira para siempre
    print("OK: un ciclo preexistente no cuelga el recalculo de fechas")


def test_hijos_suben_al_borrar_el_padre():
    """Replica lo que hace borrar_evento: el nieto se cuelga del abuelo.

    Si se quedara apuntando al evento borrado, la portada no lo muestra nunca
    (filtra por parent_id IS NULL) y el evento queda invisible.
    """
    c = base([(1, None), (2, 1), (3, 2)])
    abuelo = c.execute("SELECT parent_id FROM events WHERE id=?", (2,)).fetchone()
    c.execute("UPDATE events SET parent_id=? WHERE parent_id=?", (abuelo["parent_id"], 2))
    c.execute("DELETE FROM events WHERE id=2")
    assert c.execute("SELECT parent_id FROM events WHERE id=3").fetchone()[0] == 1
    huerfanos = c.execute("""SELECT COUNT(*) FROM events WHERE parent_id IS NOT NULL
                             AND parent_id NOT IN (SELECT id FROM events)""").fetchone()[0]
    assert huerfanos == 0, "borrar un evento no puede dejar hijos apuntando al vacio"
    print("OK: al borrar un evento sus hijos suben un nivel y no quedan huerfanos")


def main():
    #  1 ── 2 ── 3
    #   └── 4          5 suelto
    c = base([(1, None), (2, 1), (3, 2), (4, 1), (5, None)])

    assert descendientes(c, 1) == {1, 2, 3, 4}, descendientes(c, 1)
    assert descendientes(c, 2) == {2, 3}, descendientes(c, 2)
    assert descendientes(c, 3) == {3}
    assert descendientes(c, 5) == {5}, "un evento suelto es su propia descendencia"

    # Lo que importa: el nieto no puede ser padre del abuelo.
    assert 3 in descendientes(c, 1), "3 cuelga de 1, no puede ser su padre"
    # Y un evento de otra rama si puede.
    assert 5 not in descendientes(c, 1)
    assert 4 not in descendientes(c, 2), "4 es hermano de 2, sirve de padre"

    # Si la base YA tuviera un ciclo (de una corrida vieja), esto no puede
    # colgarse: el UNION corta cuando deja de aparecer gente nueva.
    c2 = base([(1, 2), (2, 1)])
    assert descendientes(c2, 1) == {1, 2}, "un ciclo preexistente tiene que terminar igual"

    print("OK: la descendencia se calcula entera y un ciclo no cuelga la consulta")


if __name__ == "__main__":
    main()
    test_fechas_suben_hasta_la_raiz()
    test_ciclo_no_cuelga_las_fechas()
    test_hijos_suben_al_borrar_el_padre()
