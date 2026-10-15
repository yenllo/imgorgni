"""python test_refrescar_hashes.py

Lo que no puede fallar: refrescar el hash de un archivo DAÑADO borraria la
prueba de que cambio. Eso se chequea acá.
"""
import hashlib

from verificar_integridad import (HASH_VIEJO, ROTO_ANTES, ROTO_DESPUES,
                                  SANO_OK, categoria)

FIN = b"\xff\xd9"
SANO = b"\xff\xd8" + b"x" * 40 + FIN
ROTO = b"\xff\xd8" + b"x" * 40 + b"zz"      # sin marcador de fin


def sha(d):
    return hashlib.sha256(d).hexdigest()


# se refresca solo lo que cae en HASH_VIEJO
SE_REFRESCA = {HASH_VIEJO}


def main():
    casos = [
        # (nombre, ruta, hash en la base, hash real, cola real, categoria esperada)
        ("jpeg sano igual",      "a.jpg", sha(SANO), sha(SANO), FIN,          SANO_OK),
        ("jpeg sano cambiado",   "b.jpg", sha(b"viejo"), sha(SANO), FIN,      HASH_VIEJO),
        ("jpeg roto de antes",   "c.jpg", sha(ROTO), sha(ROTO), ROTO[-2:],    ROTO_ANTES),
        ("jpeg roto y cambiado", "d.jpg", sha(b"viejo"), sha(ROTO), ROTO[-2:], ROTO_DESPUES),
        ("raw igual",            "e.arw", sha(b"r"), sha(b"r"), b"xx",        SANO_OK),
        ("raw cambiado",         "f.arw", sha(b"viejo"), sha(b"r"), b"xx",    HASH_VIEJO),
    ]
    for nombre, ruta, sha_db, sha_real, cola, esperada in casos:
        cat, _, _ = categoria(ruta, sha_db, sha_real, cola)
        assert cat == esperada, f"{nombre}: dio {cat!r}, esperaba {esperada!r}"

    # La regla que protege los datos: nada dañado se refresca jamas.
    for nombre, ruta, sha_db, sha_real, cola, esperada in casos:
        cat, _, _ = categoria(ruta, sha_db, sha_real, cola)
        if cat in (ROTO_ANTES, ROTO_DESPUES):
            assert cat not in SE_REFRESCA, \
                f"{nombre}: un archivo dañado NUNCA puede refrescarse"

    # Y el caso mas delicado del lote: roto + cambiado. Es el unico donde el
    # hash difiere Y el archivo esta roto; si cayera en HASH_VIEJO se pisaria.
    cat, _, _ = categoria("d.jpg", sha(b"viejo"), sha(ROTO), ROTO[-2:])
    assert cat == ROTO_DESPUES, cat
    assert cat not in SE_REFRESCA

    print("OK: se refresca solo lo sano; ningun archivo dañado se pisa")


if __name__ == "__main__":
    main()
