"""Elegir la etiqueta de reemplazo es lo unico no trivial del renombrado."""
from renombrar_eventos_calidad import mejor_etiqueta


def test_gana_la_de_contenido_mas_votada():
    fotos = [["completely dark photo", "church interior"],
             ["church interior", "museum"],
             ["completely dark photo", "church interior"]]
    assert mejor_etiqueta(fotos) == "church interior"


def test_si_todas_son_de_calidad_no_hay_reemplazo():
    """Ese es el evento genuinamente basura: se deja con su nombre."""
    assert mejor_etiqueta([["completely dark photo"],
                           ["blurry unusable photo", "completely dark photo"]]) is None
    assert mejor_etiqueta([]) is None


def test_el_empate_se_rompe_igual_siempre():
    fotos = [["zoo", "museum"], ["museum", "zoo"]]
    assert mejor_etiqueta(fotos) == "museum" == mejor_etiqueta(list(reversed(fotos)))


def test_blanco_y_negro_es_contenido_valido():
    """Es un estilo, no un defecto: puede quedar como nombre."""
    assert mejor_etiqueta([["completely dark photo", "black and white photo"]]) \
        == "black and white photo"


if __name__ == "__main__":
    test_gana_la_de_contenido_mas_votada()
    test_si_todas_son_de_calidad_no_hay_reemplazo()
    test_el_empate_se_rompe_igual_siempre()
    test_blanco_y_negro_es_contenido_valido()
    print("ok")
