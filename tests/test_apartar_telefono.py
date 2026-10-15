"""Lo unico no trivial de apartar_telefono.py es armar la ruta destino."""
import sqlite3

from apartar_telefono import planear

DEST = r"K:\_Telefono_revisar"


def _base(filas):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, file_path TEXT, "
              "camera_model TEXT, borrado INTEGER DEFAULT 0)")
    c.executemany("INSERT INTO files (id, file_path, camera_model, borrado) VALUES (?,?,?,?)",
                  filas)
    return c


def test_conserva_la_estructura_y_filtra():
    c = _base([
        (1, r"K:\Lightroom_extraido\2025\IMG-20211105-WA0000.jpg", None, 0),
        (2, r"K:\Fotos\_DSC0470.jpg", "NIKON D750", 0),           # camara: no se toca
        (3, r"K:\Fotos\web\export.jpg", None, 0),                 # desconocido: no se toca
        (4, r"K:\Viejo\Screenshot_2024.png", None, 1),            # borrada: no se toca
        (5, r"K:\_Telefono_revisar\ya\IMG-20211105-WA0001.jpg", None, 0),  # ya apartada
    ])
    plan = planear(c, DEST)
    assert plan == [(1, r"K:\Lightroom_extraido\2025\IMG-20211105-WA0000.jpg",
                     r"K:\_Telefono_revisar\Lightroom_extraido\2025\IMG-20211105-WA0000.jpg")], plan


def test_dos_origenes_distintos_no_chocan():
    """Mismo nombre en dos carpetas: al conservar el arbol, no se pisan."""
    c = _base([(1, r"K:\a\IMG-20220101-WA0001.jpg", None, 0),
               (2, r"K:\b\IMG-20220101-WA0001.jpg", None, 0)])
    destinos = [d for _, _, d in planear(c, DEST)]
    assert len(set(destinos)) == 2, destinos


def test_plano_desempata_los_nombres_repetidos():
    """Con --plano cae todo suelto en la raiz; el nombre repetido lleva sufijo."""
    c = _base([(1, r"K:\a\IMG-20220101-WA0001.jpg", None, 0),
               (2, r"K:\b\IMG-20220101-WA0001.jpg", None, 0),
               (3, r"K:\c\IMG-20220102-WA0009.jpg", None, 0),
               (4, r"K:\d\_DSC0470.jpg", "NIKON D750", 0)])   # camara: no entra
    destinos = [d for _, _, d in planear(c, DEST, plano=True)]
    assert destinos == [DEST + r"\IMG-20220101-WA0001.jpg",
                        DEST + r"\IMG-20220101-WA0001_2.jpg",
                        DEST + r"\IMG-20220102-WA0009.jpg"], destinos


def test_plano_no_remueve_lo_que_ya_esta_en_la_raiz():
    c = _base([(1, DEST + r"\IMG-20220101-WA0001.jpg", None, 0),
               (2, DEST + r"\sub\IMG-20220102-WA0009.jpg", None, 0)])
    plan = planear(c, DEST, plano=True)
    assert [p[0] for p in plan] == [2], plan       # solo la que sigue en subcarpeta


if __name__ == "__main__":
    test_conserva_la_estructura_y_filtra()
    test_dos_origenes_distintos_no_chocan()
    test_plano_desempata_los_nombres_repetidos()
    test_plano_no_remueve_lo_que_ya_esta_en_la_raiz()
    print("ok")
