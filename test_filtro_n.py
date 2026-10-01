"""El filtro por tamaño: que el corte sea variable y que aguante basura en la URL."""
import re

import web_curador


def _eventos(cliente, url):
    """Cuantos eventos lista la pagina, leido del propio contador."""
    html = cliente.get(url).get_data(as_text=True)
    m = re.search(r'>(\d+)</b><div class="apagado">eventos listados', html)
    assert m, "no encontre el contador de eventos en la pagina"
    return int(m.group(1))


def test_el_corte_es_variable():
    c = web_curador.app.test_client()
    muchos = _eventos(c, "/?f=grandes&n=20")
    pocos = _eventos(c, "/?f=grandes&n=5000")
    assert muchos > pocos, (muchos, pocos)
    assert _eventos(c, "/?f=grandes&n=0") == _eventos(c, "/?f=todos")


def test_valor_invalido_cae_en_800():
    c = web_curador.app.test_client()
    assert _eventos(c, "/?f=grandes&n=abc") == _eventos(c, "/?f=grandes&n=800")
    assert _eventos(c, "/?f=grandes&n=-5") == _eventos(c, "/?f=grandes&n=800")
    assert _eventos(c, "/?f=grandes") == _eventos(c, "/?f=grandes&n=800")


if __name__ == "__main__":
    test_el_corte_es_variable()
    test_valor_invalido_cae_en_800()
    print("ok")
