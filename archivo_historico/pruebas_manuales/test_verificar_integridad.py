"""python test_verificar_integridad.py"""
import hashlib
import os
import tempfile

from verificar_integridad import (HASH_VIEJO, ROTO_ANTES, ROTO_DESPUES,
                                  SANO_OK, clasificar, leer)

JPEG_SANO = b"\xff\xd8\xff\xe0" + b"relleno" * 20 + b"\xff\xd9"
JPEG_ROTO = b"\xff\xd8\xff\xe0" + b"relleno" * 20          # sin ffd9


def escribir(carpeta, nombre, datos):
    p = os.path.join(carpeta, nombre)
    with open(p, "wb") as f:
        f.write(datos)
    return p


def sha(d):
    return hashlib.sha256(d).hexdigest()


def main():
    d = tempfile.mkdtemp()
    sano = escribir(d, "sano.jpg", JPEG_SANO)
    roto = escribir(d, "roto.jpg", JPEG_ROTO)
    raw = escribir(d, "foto.arw", b"lo que sea")

    # leer() saca hash y final en una pasada: el final tiene que ser el de verdad
    assert leer(sano) == (sha(JPEG_SANO), b"\xff\xd9")
    assert leer(roto) == (sha(JPEG_ROTO), JPEG_ROTO[-2:])
    # y tiene que dar igual el tamano del bloque (el final puede caer partido)
    assert leer(sano, bloque=1)[1] == b"\xff\xd9", "el final se pierde con bloques chicos"
    assert leer(sano, bloque=3) == leer(sano, bloque=1 << 20)

    # el tercer valor de clasificar es 'truncado': None cuando no hay senal
    assert clasificar(sano, sha(JPEG_SANO))[2] is False
    assert clasificar(roto, sha(JPEG_ROTO))[2] is True
    assert clasificar(raw, sha(b"lo que sea"))[2] is None, "en RAW no hay marcador que mirar"

    # el hash de la base coincide con lo que hay en el disco
    assert clasificar(sano, sha(JPEG_SANO))[0] == SANO_OK
    # roto pero el hash coincide => ya estaba roto cuando se indexo
    assert clasificar(roto, sha(JPEG_ROTO))[0] == ROTO_ANTES
    # roto y ademas cambio => se rompio despues de indexarlo
    assert clasificar(roto, sha(b"otra cosa"))[0] == ROTO_DESPUES
    # sano pero con otro hash => el archivo esta bien, el hash quedo viejo
    assert clasificar(sano, sha(b"otra cosa"))[0] == HASH_VIEJO
    # un RAW sin senal independiente nunca se puede acusar de truncado
    assert clasificar(raw, sha(b"otra cosa"))[0] == HASH_VIEJO
    assert clasificar(raw, sha(b"lo que sea"))[0] == SANO_OK

    print("OK: distingue el dano viejo del nuevo, y no acusa a los RAW")


if __name__ == "__main__":
    main()
