"""El token es lo unico que separa el archivo entero de cualquiera en el wifi."""
import web_curador


def _cliente(token):
    web_curador.TOKEN = token
    return web_curador.app.test_client()


def test_sin_token_no_entra():
    c = _cliente("secreto")
    assert c.get("/").status_code == 403
    assert c.get("/temas").status_code == 403
    assert c.get("/api/fotos?vista=sueltas").status_code == 403


def test_token_malo_tampoco():
    assert _cliente("secreto").get("/?t=otro").status_code == 403


def test_con_el_token_entra_y_deja_cookie():
    c = _cliente("secreto")
    r = c.get("/?t=secreto")
    assert r.status_code == 302, r.status_code          # redirige limpiando la URL
    assert "t=secreto" not in r.headers["Location"]     # el token no queda en el link
    assert c.get("/").status_code == 200                # la cookie ya alcanza


def test_salud_queda_libre():
    """Para poder chequear que el servidor esta vivo sin la clave."""
    assert _cliente("secreto").get("/salud").status_code == 200


def test_en_loopback_no_molesta():
    web_curador.TOKEN = None
    assert web_curador.app.test_client().get("/").status_code == 200


if __name__ == "__main__":
    test_sin_token_no_entra()
    test_token_malo_tampoco()
    test_con_el_token_entra_y_deja_cookie()
    test_salud_queda_libre()
    test_en_loopback_no_molesta()
    print("ok")
