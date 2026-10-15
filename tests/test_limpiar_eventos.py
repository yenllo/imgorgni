"""El encadenado de fusiones es lo unico no trivial de limpiar_eventos.py."""
import datetime as dt

from limpiar_eventos import planear_fusiones

T = lambda h, m: dt.datetime(2025, 5, 1, h, m)


def test_encadena_y_respeta_el_hueco():
    ev = [
        (1, T(10, 0), T(10, 30), 50),   # \
        (2, T(10, 40), T(11, 0), 8),    #  |- pegados: 10 y 20 min de hueco
        (3, T(11, 20), T(11, 50), 12),  # /
        (4, T(18, 0), T(19, 0), 40),    # 6 h despues: queda solo
    ]
    assert planear_fusiones(ev, 30) == [[1, 2, 3]]
    assert planear_fusiones(ev, 5) == []          # con umbral chico no fusiona nada


def test_solapados_van_juntos():
    ev = [(1, T(9, 0), T(12, 0), 100), (2, T(10, 0), T(10, 5), 3)]
    assert planear_fusiones(ev, 0) == [[1, 2]]    # hueco negativo: mismo momento


if __name__ == "__main__":
    test_encadena_y_respeta_el_hueco()
    test_solapados_van_juntos()
    print("ok")
