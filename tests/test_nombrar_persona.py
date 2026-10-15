"""python test_nombrar_persona.py

nombrar_persona() ponia el nombre directo sin chequear si otra persona ya lo
tenia -- a diferencia de asignar_cara(), que si fusiona. Resultado: nombrar
dos clusters de cara con el mismo nombre real ("Maria") dejaba DOS persons
separadas con el mismo name, en vez de una sola con todas las caras.

Esto reproduce la logica exacta de _w() adentro de nombrar_persona() contra
una base en memoria, sin pasar por Flask (con() usa el contexto de la app).
"""
import sqlite3

ESQUEMA = """
CREATE TABLE persons (id INTEGER PRIMARY KEY, name TEXT, face_count INTEGER DEFAULT 0);
CREATE TABLE faces (id INTEGER PRIMARY KEY, person_id INTEGER);
"""


def nombrar(c, pid, nombre):
    """Misma logica que _w() en el endpoint, para poder probarla sin Flask."""
    destino = pid
    if nombre:
        otra = c.execute("SELECT id FROM persons WHERE name=? AND id!=?", (nombre, pid)).fetchone()
        if otra:
            destino = otra["id"]
            movidas = c.execute("SELECT COUNT(*) n FROM faces WHERE person_id=?", (pid,)).fetchone()["n"]
            c.execute("UPDATE faces SET person_id=? WHERE person_id=?", (destino, pid))
            c.execute("UPDATE persons SET face_count=face_count+? WHERE id=?", (movidas, destino))
            c.execute("DELETE FROM persons WHERE id=?", (pid,))
    if destino == pid:
        c.execute("UPDATE persons SET name=? WHERE id=?", (nombre or None, pid))
    c.commit()
    return destino


def _base():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(ESQUEMA)
    return c


def test_nombre_nuevo_no_fusiona():
    c = _base()
    c.execute("INSERT INTO persons VALUES (1, NULL, 3)")
    destino = nombrar(c, 1, "Renzo")
    assert destino == 1
    r = c.execute("SELECT name, face_count FROM persons WHERE id=1").fetchone()
    assert r["name"] == "Renzo" and r["face_count"] == 3
    print("OK: nombre nuevo, sin fusion, face_count intacto")


def test_nombre_repetido_fusiona_y_borra_el_viejo():
    """El caso que estaba roto: dos clusters, mismo nombre real."""
    c = _base()
    c.execute("INSERT INTO persons VALUES (1, 'Maria', 5)")
    c.execute("INSERT INTO persons VALUES (2, NULL, 3)")
    c.executemany("INSERT INTO faces VALUES (?,?)",
                  [(10, 2), (11, 2), (12, 2)])  # 3 caras del cluster 2

    destino = nombrar(c, 2, "Maria")

    assert destino == 1, "tiene que fusionar hacia la persona QUE YA TENIA el nombre"
    quedan = c.execute("SELECT * FROM persons WHERE id=2").fetchall()
    assert quedan == [], "la persona vieja (2) tiene que desaparecer, no quedar con el mismo nombre"
    r = c.execute("SELECT name, face_count FROM persons WHERE id=1").fetchone()
    assert r["face_count"] == 8, f"5+3=8 caras fusionadas, dio {r['face_count']}"
    huerfanas = c.execute("SELECT COUNT(*) n FROM faces WHERE person_id=2").fetchone()["n"]
    assert huerfanas == 0, "ninguna cara puede seguir apuntando al id borrado"
    movidas = c.execute("SELECT COUNT(*) n FROM faces WHERE person_id=1").fetchone()["n"]
    assert movidas == 3, "las 3 caras del cluster viejo tienen que apuntar ahora a la fusion"
    print("OK: nombre repetido fusiona de verdad -- una sola persona, cero huerfanas")


def test_no_hay_nombres_duplicados_en_persons_tras_fusionar():
    c = _base()
    c.execute("INSERT INTO persons VALUES (1, 'Ana', 2)")
    c.execute("INSERT INTO persons VALUES (2, NULL, 1)")
    nombrar(c, 2, "Ana")
    repetidos = c.execute(
        "SELECT name, COUNT(*) c FROM persons WHERE name IS NOT NULL GROUP BY name HAVING c>1").fetchall()
    assert repetidos == [], "no puede quedar mas de una persona con el mismo name"
    print("OK: cero nombres duplicados en persons despues de fusionar")


def test_vaciar_el_nombre_no_fusiona_con_nadie():
    """Sacar el nombre (nombre='') es un caso normal, no deberia buscar fusion."""
    c = _base()
    c.execute("INSERT INTO persons VALUES (1, 'Pepe', 4)")
    destino = nombrar(c, 1, "")
    assert destino == 1
    r = c.execute("SELECT name FROM persons WHERE id=1").fetchone()
    assert r["name"] is None
    print("OK: vaciar el nombre no dispara fusion")


if __name__ == "__main__":
    test_nombre_nuevo_no_fusiona()
    test_nombre_repetido_fusiona_y_borra_el_viejo()
    test_no_hay_nombres_duplicados_en_persons_tras_fusionar()
    test_vaciar_el_nombre_no_fusiona_con_nadie()
