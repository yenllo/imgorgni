"""Chequeo del import de duplicados.csv en web_curador.

Lo que tiene que cumplir:
  - reimportar un CSV no puede duplicar pares ni resucitar como 'pendiente'
    algo que ya decidiste;
  - el score sale de los phash del CSV, y vale SIN_SCORE cuando no se puede
    confiar (falta un phash, o el phash es degenerado). Un score -1 que se
    cuela como 0 hace que un par sin verificar aparezca como el mas seguro.

    python test_import_duplicados.py
"""
import os
import sqlite3

import pytest
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ff..ff y 00..00 difieren en los 64 bits; 0001 esta a 1 bit de 0000.
PLANO = "0000000000000000"     # el hash degenerado: lo comparten muchas fotos
CERCA = "0000000000000001"
LEJOS = "ffffffffffffffff"

CSV = f"""tipo,razon,mb_descartado,conservar,descartar,phash_conservar,phash_descartar
EXACTO,mismo sha,10.5,K:\\a\\uno.jpg,K:\\b\\uno.jpg,,
SIMILAR,phash,8.0,K:\\a\\dos.jpg,K:\\b\\dos.jpg,{PLANO},{CERCA}
EXACTO,mismo sha,10.5,K:\\A\\UNO.JPG,K:\\B\\UNO.JPG,,
SIMILAR,phash,4.0,K:\\a\\tres.jpg,K:\\b\\tres.jpg,{CERCA},{LEJOS}
"""

ESQUEMA = """CREATE TABLE files (id INTEGER PRIMARY KEY, file_path TEXT);
CREATE TABLE photo_features (photo_id INTEGER PRIMARY KEY, phash TEXT);
CREATE TABLE duplicados (id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT, razon TEXT,
    mb REAL, ruta_conservar TEXT, ruta_descartar TEXT, id_conservar INT,
    id_descartar INT, decision TEXT DEFAULT 'pendiente', score INT DEFAULT -1,
    ambos_corruptos INT);"""


@pytest.fixture
def w(tmp_path, monkeypatch):
    """web_curador con un duplicados.csv de prueba en el directorio actual.

    El archivo nacio para correrse a mano (ver main() abajo): ahi `w` era el
    modulo que se pasaba por parametro. Bajo pytest, `w` parecia una fixture
    que no existia y los tres tests daban error de setup.
    """
    (tmp_path / "duplicados.csv").write_text(CSV, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    import web_curador
    return web_curador


def _base():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(ESQUEMA)
    # 10 fotos distintas con el mismo hash plano: eso lo vuelve degenerado.
    c.executemany("INSERT INTO photo_features VALUES (?,?)",
                  [(900 + i, PLANO) for i in range(10)])
    # Las fotos del CSV tienen que existir en files: desde el 2026-08-03
    # importar_csv descarta el par si alguno de los dos lados no esta en la
    # base (antes entraban con id NULL y reventaban /duplicados/grupos).
    c.executemany("INSERT INTO files (id, file_path) VALUES (?,?)", [
        (1, r"K:\a\uno.jpg"), (2, r"K:\b\uno.jpg"),
        (3, r"K:\a\dos.jpg"), (4, r"K:\b\dos.jpg"),
        (5, r"K:\a\tres.jpg"), (6, r"K:\b\tres.jpg"),
    ])
    return c


def test_import_y_score(w):
    c = _base()
    n = w.importar_csv(c)
    assert n == 3, f"el CSV tiene 3 pares distintos (el 3ro repite el 1ro), inserto {n}"

    por_ruta = {r["ruta_conservar"]: r for r in c.execute("SELECT * FROM duplicados")}

    # sin phash en el CSV: no se puede comparar el contenido
    assert por_ruta[r"K:\a\uno.jpg"]["score"] == w.SIN_SCORE, "sin phash tiene que quedar SIN_SCORE"

    # phash degenerado de un lado: tampoco se puede confiar, aunque de distancia 1
    assert por_ruta[r"K:\a\dos.jpg"]["score"] == w.SIN_SCORE, \
        "un phash degenerado no puede producir un score bajo (era el bug de los grupos)"

    # dos phash sanos: score real
    assert por_ruta[r"K:\a\tres.jpg"]["score"] == 63, \
        f"esperaba distancia 63, dio {por_ruta[r'K:\a\tres.jpg']['score']}"

    # Decidis uno a mano, como en la web.
    c.execute("UPDATE duplicados SET decision='descartar' WHERE ruta_conservar LIKE '%uno.jpg'")
    c.commit()

    assert w.importar_csv(c) == 0, "reimportar el mismo CSV no puede insertar nada"
    filas = c.execute("SELECT ruta_conservar, decision FROM duplicados").fetchall()
    assert len(filas) == 3, f"quedaron {len(filas)} filas, se esperaban 3"
    assert sum(1 for r in filas if r["decision"] == "descartar") == 1, \
        "se perdio la decision al reimportar"
    print("OK import: sin duplicados, decisiones intactas, score desde el CSV")


def test_filtro_no_traga_sin_verificar(w):
    """El filtro de Fiabilidad no puede contar los SIN_SCORE como score 0."""
    c = _base()
    c.executemany("""INSERT INTO duplicados (tipo, decision, score) VALUES ('SIMILAR','pendiente',?)""",
                  [(w.SIN_SCORE,), (0,), (3,), (9,)])
    c.commit()
    crudo = c.execute("SELECT COUNT(*) n FROM duplicados WHERE score <= 0").fetchone()["n"]
    bueno = c.execute("SELECT COUNT(*) n FROM duplicados WHERE score >= 0 AND score <= 0").fetchone()["n"]
    assert crudo == 2, "el comparador viejo se traga el -1 (esto documenta el bug)"
    assert bueno == 1, f"'solo iguales' tiene que devolver 1 par, devolvio {bueno}"
    print("OK filtro: SIN_SCORE ya no pasa por 'solo iguales'")


def test_recalcular_scores(w):
    """Los pares viejos en -1 se completan con los phash de photo_features."""
    c = _base()
    c.executemany("INSERT INTO photo_features VALUES (?,?)",
                  [(1, CERCA), (2, LEJOS), (3, CERCA), (4, PLANO)])
    c.executemany("""INSERT INTO duplicados (tipo, decision, score, id_conservar, id_descartar)
                     VALUES ('SIMILAR','pendiente',?,?,?)""",
                  [(w.SIN_SCORE, 1, 2),   # sanos: se completa
                   (w.SIN_SCORE, 1, 3),   # sanos e iguales: 0
                   (w.SIN_SCORE, 1, 4),   # uno degenerado: sigue sin verificar
                   (5, 1, 2)])            # ya tenia score: no se toca
    c.commit()
    n = w.recalcular_scores(c)
    assert n == 2, f"tenia que completar 2 pares, completo {n}"
    scores = [r["score"] for r in c.execute(
        "SELECT score FROM duplicados ORDER BY id")]
    assert scores == [63, 0, w.SIN_SCORE, 5], f"scores quedaron {scores}"
    print("OK recalcular: completa los sanos y deja SIN_SCORE los degenerados")


def main():
    tmp = tempfile.mkdtemp()
    os.chdir(tmp)
    with open("duplicados.csv", "w", encoding="utf-8") as f:
        f.write(CSV)
    import web_curador as w
    test_import_y_score(w)
    test_filtro_no_traga_sin_verificar(w)
    test_recalcular_scores(w)


if __name__ == "__main__":
    main()
